"""
Offline import scorecard — ingestion stage.

Runs the deterministic extraction layer (app.modules.sales.importer.extractor)
over every file in a folder and reports, per file, how much content we get out
— WITHOUT calling any LLM. This is the regression guard for the import
pipeline's ingestion stage: it catches the failure modes that silently produced
empty extractions (wrong <dimension>, mislabeled extensions, multi-sheet files
where only the first sheet was read).

Usage:
    .venv/bin/python import_scorecard.py "/path/to/folder" [--json out.json]

A file is flagged EMPTY when extraction yields almost no text — the signal that
the ingestion stage failed before the AI ever runs.
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# Make `app` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.sales.importer.extractor import (  # noqa: E402
    _sniff_format,
    extract,
    probe_size,
)

SUPPORTED = {"pdf", "xlsx", "xls", "csv"}
EMPTY_CHAR_THRESHOLD = 50  # below this, extraction effectively failed


def score_file(path: str, *, run_llm: bool = False, skip_pdf: bool = False) -> dict:
    content = open(path, "rb").read()
    filename = os.path.basename(path)
    ext = Path(filename).suffix.lower().lstrip(".")
    rec: dict = {"file": filename, "ext": ext, "bytes": len(content)}
    try:
        fmt = _sniff_format(filename, content)
        rec["sniffed"] = fmt
        rec["ext_mismatch"] = (fmt != ext and fmt in ("pdf", "xlsx", "xls"))
        pages, rows = probe_size(filename, content)
        rec["probe_pages"], rec["probe_rows"] = pages, rows
        res = extract(filename, content, detect_fos=False)
        if res.is_pdf:
            rec["sheets"] = None
            rec["chars"] = len(res.raw_bytes or b"")  # PDF goes to MinerU as bytes
            rec["rows_out"] = None
            rec["empty"] = (rec["chars"] < EMPTY_CHAR_THRESHOLD)
        else:
            text = res.raw_text or ""
            rec["sheets"] = text.count("### SHEET:") or 1
            rec["chars"] = len(text)
            rec["rows_out"] = res.total_rows
            rec["empty"] = (len(text.strip()) < EMPTY_CHAR_THRESHOLD)

        if run_llm and not (res.is_pdf and skip_pdf):
            from app.modules.sales.importer.llm_parser import (
                LLMParseRequest,
                parse_with_llm,
            )
            from app.modules.sales.import_service import _resolve_report_month

            resp = parse_with_llm(
                LLMParseRequest(
                    raw_text=res.raw_text,
                    pdf_bytes=res.raw_bytes,
                    is_pdf=res.is_pdf,
                    log_prefix="[score]",
                )
            )
            month = _resolve_report_month(resp.report_month, filename)
            # "extractable" = rows that satisfy the non-DB gates: product + qty,
            # and a date (own date OR a resolvable report month for the file).
            extractable = sum(
                1
                for d in resp.rows
                if (d.get("product_name_raw") or "").strip()
                and d.get("sale_qty") not in (None, "", "null")
                and (d.get("sale_date") or month)
            )
            rec["llm_rows"] = len(resp.rows)
            rec["llm_month"] = month
            rec["llm_extractable"] = extractable
            rec["llm_empty"] = (len(resp.rows) == 0)
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["empty"] = True
    return rec


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    root = args[0]
    json_out = None
    if "--json" in sys.argv:
        json_out = sys.argv[sys.argv.index("--json") + 1]
    run_llm = "--llm" in sys.argv          # also run the AI extraction per file
    skip_pdf = "--skip-pdf" in sys.argv    # skip PDFs (avoids MinerU cost) under --llm

    files: list[str] = []
    for dirpath, _, filenames in os.walk(root):
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            files.append(os.path.join(dirpath, fn))
    files.sort()

    results = [score_file(p, run_llm=run_llm, skip_pdf=skip_pdf) for p in files]

    empties = [r for r in results if r.get("empty")]
    errors = [r for r in results if r.get("error")]
    mism = [r for r in results if r.get("ext_mismatch")]
    multi = [r for r in results if (r.get("sheets") or 0) and (r.get("sheets") or 0) > 1]

    print(f"\n{'FILE':<58} {'ext→sniff':<12} {'sheets':>6} {'rows':>6} {'chars':>8}  flag")
    print("-" * 100)
    for r in results:
        flag = ""
        if r.get("error"):
            flag = "ERROR: " + r["error"][:40]
        elif r.get("empty"):
            flag = "** EMPTY **"
        elif r.get("ext_mismatch"):
            flag = f"mislabeled (.{r['ext']} is really {r['sniffed']})"
        es = f"{r['ext']}→{r.get('sniffed','?')}"
        llm = ""
        if "llm_rows" in r:
            llm = f" | llm_rows={r['llm_rows']} extractable={r['llm_extractable']} month={r.get('llm_month')}"
        print(
            f"{r['file'][:57]:<58} {es:<12} "
            f"{str(r.get('sheets') or '-'):>6} {str(r.get('rows_out') or '-'):>6} "
            f"{r.get('chars',0):>8}  {flag}{llm}"
        )

    print("\n=== SUMMARY ===")
    print(f"total files          : {len(results)}")
    print(f"EMPTY (ingest failed): {len(empties)}")
    print(f"errors               : {len(errors)}")
    print(f"extension mismatches : {len(mism)}  {[r['file'] for r in mism]}")
    print(f"multi-sheet files    : {len(multi)}  {[(r['file'], r['sheets']) for r in multi]}")
    if any("llm_rows" in r for r in results):
        llm_ran = [r for r in results if "llm_rows" in r]
        llm_empty = [r for r in llm_ran if r.get("llm_empty")]
        total_extractable = sum(r.get("llm_extractable", 0) for r in llm_ran)
        print(f"LLM ran on           : {len(llm_ran)} files")
        print(f"LLM produced 0 rows  : {len(llm_empty)}  {[r['file'] for r in llm_empty]}")
        print(f"total extractable rows: {total_extractable}")
    if empties:
        print("\nEMPTY files (need attention):")
        for r in empties:
            print(f"  - {r['file']}  ({r.get('error') or 'no content extracted'})")

    if json_out:
        json.dump(results, open(json_out, "w"), indent=1, default=str)
        print(f"\nwrote {json_out}")


if __name__ == "__main__":
    main()
