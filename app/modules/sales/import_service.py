"""
ImportService — Approach G: Gemini Files API + Backend Entity Resolution.

Flow:
  upload  → create ImportJob (status=processing)
          → background: extract → single LLM call → fuzzy resolve → validate → ready/partial
  preview → return structured_rows + extraction_warnings
  commit  → insert confirmed rows into secondary_sales
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date

import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrDoctorAllocation, MrHeadquarterAllocation
from app.models.doctor import Doctor, DoctorMedicalStore
from app.models.import_job import ImportJob, ImportJobStatus, ImportSourceType
from app.models.master import Headquarter, Product
from app.models.sale import SecondarySale
from app.models.stockist import MedicalStore
from app.models.user import User
from app.modules.sales.importer.extractor import ExtractionResult, extract
from app.modules.sales.importer.llm_parser import LLMParseRequest, LLMParseResponse, parse_with_llm
from app.modules.sales.importer.validator import validate_rows

logger = logging.getLogger(__name__)


def _job_log_prefix(job_id: uuid.UUID | None) -> str:
    """Short, greppable prefix so all log lines for one job can be filtered."""
    if job_id is None:
        return "[job=?]"
    return f"[job={str(job_id)[:8]}]"


# ---------------------------------------------------------------------------
# Lightweight entity struct (shared between LLM layer and fuzzy resolver)
# ---------------------------------------------------------------------------

@dataclass
class EntityCandidate:
    id: str
    name: str


class ImportService:

    # ------------------------------------------------------------------
    # Step 1: create the job record and return immediately
    # ------------------------------------------------------------------

    async def create_job(
        self,
        db: AsyncSession,
        *,
        filename: str,
        content: bytes,
        uploaded_by: uuid.UUID,
    ) -> ImportJob:
        ext = filename.rsplit(".", 1)[-1].lower()
        try:
            source_type = ImportSourceType(ext)
        except ValueError:
            raise ValueError(f"Unsupported file format: {ext!r}. Supported: pdf, csv, xlsx, xls")

        file_hash = hashlib.sha256(content).hexdigest()

        # `mr_id` on ImportJob is no longer set at upload time. Per-row MR
        # resolution happens in _do_process via store → doctor → MR allocation.
        job = ImportJob(
            filename=filename,
            file_hash=file_hash,
            source_type=source_type,
            uploaded_by=uploaded_by,
            mr_id=None,
            status=ImportJobStatus.processing,
        )
        db.add(job)
        await db.flush()
        await db.refresh(job)
        logger.info(
            "%s create_job: created filename=%r ext=%s bytes=%d sha256=%s uploaded_by=%s",
            _job_log_prefix(job.id), filename, ext, len(content),
            file_hash[:12], str(uploaded_by)[:8],
        )
        return job

    # ------------------------------------------------------------------
    # Step 2: process (run in background task)
    # ------------------------------------------------------------------

    async def process_job(
        self,
        db: AsyncSession,
        job: ImportJob,
        content: bytes,
    ) -> None:
        prefix = _job_log_prefix(job.id)
        t_total = time.perf_counter()
        logger.info(
            "%s process_job: starting filename=%r bytes=%d",
            prefix, job.filename, len(content),
        )
        try:
            await self._do_process(db, job, content)
            logger.info(
                "%s process_job: ok status=%s total_rows=%s warnings=%d total_ms=%.0f",
                prefix,
                getattr(job.status, "value", str(job.status)),
                job.total_rows,
                len(job.extraction_warnings or []),
                (time.perf_counter() - t_total) * 1000,
            )
        except Exception as exc:
            logger.exception(
                "%s process_job: FAILED after total_ms=%.0f (%s: %s)",
                prefix, (time.perf_counter() - t_total) * 1000,
                type(exc).__name__, exc,
            )
            from app.core.database import AsyncSessionLocal
            err_msg = str(exc)
            try:
                async with AsyncSessionLocal() as fresh:
                    async with fresh.begin():
                        fresh_job = await fresh.get(ImportJob, job.id)
                        if fresh_job is not None:
                            fresh_job.status = ImportJobStatus.failed
                            fresh_job.error_message = err_msg
                logger.info("%s process_job: failure persisted to DB", prefix)
            except Exception:
                logger.exception(
                    "%s process_job: failed to persist failure status (using current session as fallback)",
                    prefix,
                )
                try:
                    job.status = ImportJobStatus.failed
                    job.error_message = err_msg
                    await db.flush()
                except Exception:
                    logger.exception(
                        "%s process_job: fallback persistence ALSO failed",
                        prefix,
                    )

    async def _do_process(
        self,
        db: AsyncSession,
        job: ImportJob,
        content: bytes,
    ) -> None:
        prefix = _job_log_prefix(job.id)

        # 1. Extract: PDFs → raw bytes; tabular → minimal CSV text.
        #    FOS-hint detection is no longer needed for resolution (mr_id is
        #    now derived from medical_store → doctor → MR allocation), so we
        #    skip the second PDF parse entirely. Tabular extraction picks up
        #    the FOS name for free as it scans rows; we keep that for display.
        t_step = time.perf_counter()
        result: ExtractionResult = extract(
            job.filename, content, detect_fos=False, log_prefix=prefix,
        )
        job.raw_text = result.raw_text or ""
        if result.detected_fos_name:
            job.detected_fos_name = result.detected_fos_name
        logger.info(
            "%s step1 extract: is_pdf=%s text_chars=%d pdf_bytes=%d "
            "total_pages=%s total_rows=%s elapsed_ms=%.0f",
            prefix, result.is_pdf,
            len(result.raw_text or ""), len(result.raw_bytes or b""),
            result.total_pages, result.total_rows,
            (time.perf_counter() - t_step) * 1000,
        )

        # 2. Single LLM call — no entity lists, just file/text
        req = LLMParseRequest(
            raw_text=result.raw_text,
            pdf_bytes=result.raw_bytes,
            is_pdf=result.is_pdf,
            detected_fos_name=None,
            log_prefix=prefix,
        )
        t_step = time.perf_counter()
        resp: LLMParseResponse = await asyncio.to_thread(parse_with_llm, req)
        logger.info(
            "%s step2 parse_with_llm: model=%s rows=%d elapsed_ms=%.0f",
            prefix, resp.model_used, len(resp.rows),
            (time.perf_counter() - t_step) * 1000,
        )

        # 3. Load entity catalogues for backend fuzzy matching. MR names ARE
        #    matched from the file when present (Fso / TERRITORY column); MRs
        #    not named in the file fall back to the store→doctor allocation chain.
        t_step = time.perf_counter()
        products = await self._get_all_products(db)
        medical_stores = await self._get_all_stores(db)
        doctors = await self._get_all_doctors(db)
        mrs = await self._get_all_mrs(db)
        logger.info(
            "%s step3 load_entities: products=%d stores(+aliases)=%d doctors=%d mrs=%d "
            "elapsed_ms=%.0f",
            prefix, len(products), len(medical_stores), len(doctors), len(mrs),
            (time.perf_counter() - t_step) * 1000,
        )

        # 4. Fuzzy resolve product / store / doctor / MR names → UUIDs
        t_step = time.perf_counter()
        resolved_rows = self._fuzzy_resolve_entities(
            resp.rows, products, medical_stores, doctors, mrs,
            log_prefix=prefix,
        )
        logger.info(
            "%s step4 fuzzy_resolve: rows=%d elapsed_ms=%.0f",
            prefix, len(resolved_rows),
            (time.perf_counter() - t_step) * 1000,
        )

        # 4b. Fill sale_date for dateless rows from the report month.
        t_step = time.perf_counter()
        report_month, dates_estimated = self._apply_report_month(
            resolved_rows, resp.report_month, job.filename, log_prefix=prefix,
        )
        logger.info(
            "%s step4b report_month: month=%s dates_estimated=%d elapsed_ms=%.0f",
            prefix, report_month, dates_estimated,
            (time.perf_counter() - t_step) * 1000,
        )

        # 5. Resolve mr_id from each row's medical_store_id via:
        #       doctor_medical_stores → mr_doctor_allocations (is_active)
        #    Returns warnings for stores with 0 or multiple distinct MRs.
        t_step = time.perf_counter()
        mr_resolution_warnings = await self._resolve_mrs_from_stores(
            db, resolved_rows, log_prefix=prefix,
        )
        logger.info(
            "%s step5 resolve_mrs_from_stores: warnings=%d elapsed_ms=%.0f",
            prefix, len(mr_resolution_warnings),
            (time.perf_counter() - t_step) * 1000,
        )

        # 6. Deterministic validation + type coercion
        t_step = time.perf_counter()
        validated = await validate_rows(db, resolved_rows, log_prefix=prefix)
        logger.info(
            "%s step6 validate: rows=%d elapsed_ms=%.0f",
            prefix, len(validated),
            (time.perf_counter() - t_step) * 1000,
        )

        # 6b. Display names follow DB for resolved ids; null ids → null names (user fills in UI)
        t_step = time.perf_counter()
        await self._hydrate_raw_names_from_db(db, validated, log_prefix=prefix)
        logger.info(
            "%s step6b hydrate_raw_names: elapsed_ms=%.0f",
            prefix, (time.perf_counter() - t_step) * 1000,
        )

        # 6c. Auto-populate derived fields (division/location/hq/state + doctor auto-fill)
        t_step = time.perf_counter()
        await self._enrich_derived_fields(db, validated, log_prefix=prefix)
        logger.info(
            "%s step6c enrich_derived: elapsed_ms=%.0f",
            prefix, (time.perf_counter() - t_step) * 1000,
        )

        # 7. Assemble warnings (chunk failures first, then MR-resolution issues).
        #    NOTE: we deliberately no longer warn that "MR X has no headquarter
        #    allocation — rows will be skipped". headquarter/state/division are
        #    now derived from the medical store (see _enrich_derived_fields), NOT
        #    from the MR's HQ allocation, so those rows DO commit. That old
        #    warning was a false alarm. Genuinely non-committable rows (missing
        #    product/store/MR or an underivable HQ chain) are surfaced per-row by
        #    the validator and by commit_job's skipped_rows.
        warnings: list[str] = list(resp.warnings) + list(mr_resolution_warnings)

        # 8. Persist — with loud failure signalling. "Extracted nothing" or
        #    "nothing committable" must NEVER masquerade as a silent 'ready'.
        valid_n = sum(1 for r in validated if r.get("is_valid"))
        invalid_n = len(validated) - valid_n

        if dates_estimated:
            warnings.insert(
                0,
                f"{dates_estimated} row(s) had no date in the file — assigned to "
                f"{report_month or 'the report month'} (1st of month). Adjust in preview if needed.",
            )

        job.structured_rows = validated
        job.total_rows = len(validated)
        job.model_used = resp.model_used or None
        job.chunks_total = 1
        job.chunks_succeeded = 1

        if len(validated) == 0:
            job.status = ImportJobStatus.failed
            job.error_message = (
                "No sale rows could be extracted from this file. Confirm it is an "
                "itemized secondary-sales report (not a scanned image or a summary "
                "with no product lines), then re-upload."
            )
            job.extraction_warnings = warnings or None
        elif valid_n == 0:
            warnings.insert(
                0,
                f"None of the {len(validated)} extracted row(s) are ready to commit — "
                "assign the missing product / medical store / MR in preview.",
            )
            job.status = ImportJobStatus.partial
            job.extraction_warnings = warnings
        else:
            job.extraction_warnings = warnings if warnings else None
            job.status = ImportJobStatus.partial if warnings else ImportJobStatus.ready
        await db.flush()
        logger.info(
            "%s step8 persist: status=%s total_rows=%d valid=%d invalid=%d warnings=%d",
            prefix, job.status.value if hasattr(job.status, "value") else str(job.status),
            len(validated), valid_n, invalid_n, len(warnings),
        )

    # ------------------------------------------------------------------
    # Fuzzy entity resolution
    # ------------------------------------------------------------------

    def _fuzzy_resolve_entities(
        self,
        rows: list[dict],
        products: list[EntityCandidate],
        medical_stores: list[EntityCandidate],
        doctors: list[EntityCandidate],
        mrs: list[EntityCandidate],
        *,
        log_prefix: str = "",
    ) -> list[dict]:
        """
        Five-level fuzzy match raw LLM-extracted names to entity UUIDs:

          1. Exact match on raw (case-insensitive)
          2. Normalized exact   (upper-cased, punctuation stripped, whitespace collapsed)
          3. Substring containment (either direction)
          4. First significant token (first word >=4 chars present in candidate tokens)
          5. difflib.get_close_matches with cutoff 0.6

        Unresolved names are left as None — the frontend handles manual assignment.

        MR names ARE matched here when the file carries them (e.g. a
        Fso/TERRITORY column). Rows whose MR is not named in the file keep
        mr_id=None and are resolved later via `_resolve_mrs_from_stores`
        (medical-store → doctor → MR allocation chain).

        Implementation note: levels 1-2 are O(1) dict lookups; levels 3-4 are
        linear scans (kept on the precomputed normalized list); level 5 uses
        difflib. Per-row results are also memoized so repeated raw names in
        large files cost nothing after the first row.
        """
        prod_uppers = [(c.name or "").upper() for c in products]
        store_uppers = [(c.name or "").upper() for c in medical_stores]
        doc_uppers = [(c.name or "").upper() for c in doctors]
        mr_uppers = [(c.name or "").upper() for c in mrs]

        prod_norms = [_normalize(c.name) for c in products]
        store_norms = [_normalize(c.name) for c in medical_stores]
        doc_norms = [_normalize(c.name) for c in doctors]
        mr_norms = [_normalize(c.name) for c in mrs]

        # Build O(1) lookup dicts for levels 1 & 2. First-occurrence wins so
        # behaviour matches the original linear-scan tie-breaking exactly.
        def _build_dict(keys: list[str], cands: list[EntityCandidate]) -> dict[str, str]:
            d: dict[str, str] = {}
            for i, k in enumerate(keys):
                if k and k not in d:
                    d[k] = cands[i].id
            return d

        prod_upper_map = _build_dict(prod_uppers, products)
        prod_norm_map = _build_dict(prod_norms, products)
        store_upper_map = _build_dict(store_uppers, medical_stores)
        store_norm_map = _build_dict(store_norms, medical_stores)
        doc_upper_map = _build_dict(doc_uppers, doctors)
        doc_norm_map = _build_dict(doc_norms, doctors)
        mr_upper_map = _build_dict(mr_uppers, mrs)
        mr_norm_map = _build_dict(mr_norms, mrs)

        # Per-entity-type cache so repeated raw names don't redo the heavy work.
        _SENTINEL = object()
        prod_cache: dict[str, str | None] = {}
        store_cache: dict[str, str | None] = {}
        doc_cache: dict[str, str | None] = {}
        mr_cache: dict[str, str | None] = {}

        def match(
            raw: str | None,
            candidates: list[EntityCandidate],
            upper_map: dict[str, str],
            norm_map: dict[str, str],
            norms: list[str],
            cache: dict[str, str | None],
            *,
            strict: bool = False,
        ) -> str | None:
            if not raw or not candidates:
                return None

            cached = cache.get(raw, _SENTINEL)
            if cached is not _SENTINEL:
                return cached  # type: ignore[return-value]

            ru = raw.upper().strip()
            rn = _normalize(raw)

            # Level 1: exact (case-insensitive on the raw strings) — O(1)
            hit = upper_map.get(ru)
            if hit is not None:
                cache[raw] = hit
                return hit

            # Level 2: normalized exact (punctuation + whitespace stripped) — O(1)
            if rn:
                hit = norm_map.get(rn)
                if hit is not None:
                    cache[raw] = hit
                    return hit

            # strict mode (people / MRs): exact + normalized-exact ONLY. Looser
            # matching wrongly maps a sheet FSO like 'JAYESHVAGHASIA' onto an
            # unrelated user 'Jayesh Gandhi'. Never guess a person's identity.
            if strict:
                cache[raw] = None
                return None

            # Level 3: substring containment on normalized form
            if rn:
                for i, cn in enumerate(norms):
                    if cn and (rn in cn or cn in rn):
                        cache[raw] = candidates[i].id
                        return candidates[i].id

            # Level 4: first significant token
            raw_tokens = [t for t in rn.split() if len(t) >= 4]
            if raw_tokens:
                ft = raw_tokens[0]
                for i, cn in enumerate(norms):
                    if ft in cn.split():
                        cache[raw] = candidates[i].id
                        return candidates[i].id

            # Level 5: difflib close match (cutoff 0.6)
            hits = difflib.get_close_matches(rn, norms, n=1, cutoff=0.6)
            if hits:
                resolved_id = candidates[norms.index(hits[0])].id
                cache[raw] = resolved_id
                return resolved_id

            cache[raw] = None
            return None

        resolved: list[dict] = []
        # Counters for visibility into how rows fared.
        prod_seen = prod_resolved = 0
        store_seen = store_resolved = 0
        doc_seen = doc_resolved = 0
        mr_seen = mr_resolved = 0

        for row in rows:
            r = dict(row)

            if not r.get("product_id"):
                if r.get("product_name_raw"):
                    prod_seen += 1
                r["product_id"] = match(
                    r.get("product_name_raw"), products,
                    prod_upper_map, prod_norm_map, prod_norms, prod_cache,
                )
                if r["product_id"]:
                    prod_resolved += 1

            if not r.get("medical_store_id"):
                if r.get("customer_name_raw"):
                    store_seen += 1
                r["medical_store_id"] = match(
                    r.get("customer_name_raw"), medical_stores,
                    store_upper_map, store_norm_map, store_norms, store_cache,
                )
                if r["medical_store_id"]:
                    store_resolved += 1

            # MR from the file (Fso/TERRITORY column) when present; otherwise
            # left None for `_resolve_mrs_from_stores` to fill from allocations.
            if not r.get("mr_id"):
                if r.get("mr_name_raw"):
                    mr_seen += 1
                r["mr_id"] = match(
                    r.get("mr_name_raw"), mrs,
                    mr_upper_map, mr_norm_map, mr_norms, mr_cache,
                    strict=True,
                )
                if r["mr_id"]:
                    mr_resolved += 1

            if not r.get("doctor_id"):
                if r.get("doctor_name_raw"):
                    doc_seen += 1
                r["doctor_id"] = match(
                    r.get("doctor_name_raw"), doctors,
                    doc_upper_map, doc_norm_map, doc_norms, doc_cache,
                )
                if r["doctor_id"]:
                    doc_resolved += 1

            resolved.append(r)

        logger.info(
            "%s fuzzy_resolve: product %d/%d, store %d/%d, doctor %d/%d, mr %d/%d "
            "(unique_raw_names: prod=%d store=%d doc=%d mr=%d)",
            log_prefix,
            prod_resolved, prod_seen,
            store_resolved, store_seen,
            doc_resolved, doc_seen,
            mr_resolved, mr_seen,
            len(prod_cache), len(store_cache), len(doc_cache), len(mr_cache),
        )

        return resolved

    # ------------------------------------------------------------------
    # Step 3: commit confirmed rows
    # ------------------------------------------------------------------

    async def commit_job(
        self,
        db: AsyncSession,
        job: ImportJob,
        confirmed_rows: list[dict],
        committed_by: User,
    ) -> dict:
        """
        Insert confirmed rows into secondary_sales.
        Returns a structured summary with counts and per-row skip reasons.
        """
        from app.modules.sales.service import resolve_manager_chain

        prefix = _job_log_prefix(job.id)
        t_total = time.perf_counter()
        logger.info(
            "%s commit_job: starting confirmed_rows=%d committed_by=%s",
            prefix, len(confirmed_rows), str(committed_by.id)[:8],
        )

        # Re-apply the report-month fallback before re-validating. The synthesized
        # date is set at preview time, but the client may round-trip the rows with
        # the date field still empty for records that had none in the file — without
        # this, those rows would fail validation here and silently never commit.
        _, recommitted_dates = self._apply_report_month(
            confirmed_rows, None, job.filename, log_prefix=prefix,
        )
        if recommitted_dates:
            logger.info(
                "%s commit_job: re-filled %d dateless row(s) from report month",
                prefix, recommitted_dates,
            )

        t_step = time.perf_counter()
        validated = await validate_rows(db, confirmed_rows, log_prefix=prefix)
        logger.info(
            "%s commit_job: re-validate elapsed_ms=%.0f",
            prefix, (time.perf_counter() - t_step) * 1000,
        )
        # `_hydrate_raw_names_from_db` is intentionally skipped here:
        # the commit response only returns counts + skipped_rows, and the raw
        # name fields are not written to `secondary_sales`. Re-fetching them
        # was pure dead work.
        t_step = time.perf_counter()
        await self._enrich_derived_fields(db, validated, log_prefix=prefix)
        logger.info(
            "%s commit_job: enrich elapsed_ms=%.0f",
            prefix, (time.perf_counter() - t_step) * 1000,
        )

        # Pre-compute manager-chain snapshot per unique mr_id to avoid repeating
        # the same recursive CTE for every row of the same MR.
        chain_cache: dict[uuid.UUID, dict[str, uuid.UUID | None]] = {}

        # Pre-load every product referenced by valid rows in a single query —
        # the previous code did `await db.get(Product, pid)` per row, causing
        # N round-trips on the first encounter of each product_id.
        product_ids_needed: set[uuid.UUID] = set()
        for row in validated:
            if row.get("skip") or not row.get("is_valid"):
                continue
            pid_str = row.get("product_id")
            if pid_str:
                try:
                    product_ids_needed.add(uuid.UUID(str(pid_str)))
                except (ValueError, AttributeError):
                    pass

        t_step = time.perf_counter()
        product_cache: dict[uuid.UUID, Product] = {}
        if product_ids_needed:
            rs = (
                await db.execute(
                    select(Product).where(Product.id.in_(product_ids_needed))
                )
            ).scalars().all()
            product_cache = {p.id: p for p in rs}
        logger.info(
            "%s commit_job: preload products distinct=%d cached=%d elapsed_ms=%.0f",
            prefix, len(product_ids_needed), len(product_cache),
            (time.perf_counter() - t_step) * 1000,
        )

        # Block whole commit if any non-skipped row still lacks product, store, or MR id
        bad_indexes = [
            idx
            for idx, row in enumerate(validated)
            if not row.get("skip")
            and (not row.get("product_id") or not row.get("medical_store_id") or not row.get("mr_id"))
        ]
        if bad_indexes:
            logger.warning(
                "%s commit_job: blocked — %d row(s) missing required ids (first few: %s)",
                prefix, len(bad_indexes), bad_indexes[:10],
            )
            raise ValueError(
                "Cannot commit: some rows are missing required product_id, medical_store_id, or mr_id. "
                "Assign them in preview (skipped rows are ignored). "
                f"Affected row_index values (0-based): {bad_indexes[:40]}"
                + ("..." if len(bad_indexes) > 40 else "")
            )

        skipped_rows: list[dict] = []
        # Accumulate insertable rows and emit a single bulk INSERT at the end —
        # replaces 1 INSERT per row with 1 multi-row INSERT.
        sale_values: list[dict] = []
        skip_reason_hist: Counter[str] = Counter()
        t_loop = time.perf_counter()

        for idx, row in enumerate(validated):
            row_errors: list[str] = list(row.get("errors") or [])

            def _skip(reason: str) -> None:
                row_errors.append(reason)
                skip_reason_hist[reason.split(" — ")[0][:48]] += 1
                skipped_rows.append({
                    "row_index": idx,
                    "product_name_raw": row.get("product_name_raw"),
                    "customer_name_raw": row.get("customer_name_raw"),
                    "errors": row_errors,
                })

            if row.get("skip"):
                _skip("row marked skip=true")
                continue
            if not row.get("is_valid"):
                skip_reason_hist["validation failure"] += 1
                skipped_rows.append({
                    "row_index": idx,
                    "product_name_raw": row.get("product_name_raw"),
                    "customer_name_raw": row.get("customer_name_raw"),
                    "errors": row_errors or ["row failed validation"],
                })
                continue

            mr_id = uuid.UUID(row["mr_id"]) if row.get("mr_id") else None
            product_id = uuid.UUID(row["product_id"]) if row.get("product_id") else None

            if mr_id is None or product_id is None:
                _skip("missing mr_id or product_id")
                continue

            location_id = uuid.UUID(row["location_id"]) if row.get("location_id") else None
            hq_id = uuid.UUID(row["headquarter_id"]) if row.get("headquarter_id") else None
            state_id = uuid.UUID(row["state_id"]) if row.get("state_id") else None
            division_id = uuid.UUID(row["division_id"]) if row.get("division_id") else None

            # location_id is optional (nullable on secondary_sales); the
            # geographic dimension that *must* resolve is headquarter_id.
            if not all([hq_id, state_id, division_id]):
                _skip(
                    "could not derive headquarter chain — ensure the medical store "
                    "(or doctor) has a headquarter_id, and the product has a division"
                )
                continue

            product = product_cache.get(product_id)
            if product is None:
                _skip(f"product {product_id} not found")
                continue

            ptr = row.get("ptr") or float(product.ptr)
            pts = float(product.pts) if product.pts else None
            mrp = row.get("mrp") or float(product.mrp)

            sale_date = row["sale_date"]
            if isinstance(sale_date, str):
                sale_date = date.fromisoformat(sale_date)

            chain = chain_cache.get(mr_id)
            if chain is None:
                chain = await resolve_manager_chain(db, mr_id)
                chain_cache[mr_id] = chain

            sale_values.append({
                "id": uuid.uuid4(),
                "mr_id": mr_id,
                "asm_id": chain["asm_id"],
                "rsm_id": chain["rsm_id"],
                "state_head_id": chain["state_head_id"],
                "product_id": product_id,
                "doctor_id": uuid.UUID(row["doctor_id"]) if row.get("doctor_id") else None,
                "medical_store_id": uuid.UUID(row["medical_store_id"]) if row.get("medical_store_id") else None,
                "division_id": division_id,
                "headquarter_id": hq_id,
                "location_id": location_id,
                "state_id": state_id,
                "sale_date": sale_date,
                "sale_qty": float(row["sale_qty"]),
                "free_qty": float(row.get("free_qty") or 0),
                "ptr": ptr,
                "pts": pts,
                "mrp": mrp,
                "reported_amount": row.get("reported_amount"),
                "bill_ref": row.get("bill_ref"),
                "batch": row.get("batch"),
                "pack": row.get("pack"),
                "special_price": None,
                "remarks": row.get("remarks"),
                "is_active": True,
            })

        loop_ms = (time.perf_counter() - t_loop) * 1000
        logger.info(
            "%s commit_job: row-loop done sale_values=%d skipped=%d "
            "manager_chains_resolved=%d elapsed_ms=%.0f skip_reasons=%s",
            prefix, len(sale_values), len(skipped_rows),
            len(chain_cache), loop_ms,
            dict(skip_reason_hist) if skip_reason_hist else {},
        )

        committed = len(sale_values)
        if sale_values:
            from sqlalchemy import insert as sa_insert
            t_insert = time.perf_counter()
            try:
                await db.execute(sa_insert(SecondarySale), sale_values)
            except Exception:
                logger.exception(
                    "%s commit_job: bulk INSERT failed (rows=%d)",
                    prefix, len(sale_values),
                )
                raise
            logger.info(
                "%s commit_job: bulk INSERT ok rows=%d elapsed_ms=%.0f",
                prefix, committed, (time.perf_counter() - t_insert) * 1000,
            )

        job.committed_count = committed
        if committed > 0:
            job.status = ImportJobStatus.committed
        await db.flush()

        logger.info(
            "%s commit_job: done status=%s committed=%d skipped=%d total=%d total_ms=%.0f",
            prefix,
            job.status.value if hasattr(job.status, "value") else str(job.status),
            committed, len(skipped_rows), len(validated),
            (time.perf_counter() - t_total) * 1000,
        )

        return {
            "committed": committed,
            "skipped": len(skipped_rows),
            "total": len(validated),
            "skipped_rows": skipped_rows,
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        }

    # ------------------------------------------------------------------
    # Entity loaders  (for fuzzy matching — no pre-filtering needed)
    # ------------------------------------------------------------------

    async def _get_all_products(self, db: AsyncSession) -> list[EntityCandidate]:
        t0 = time.perf_counter()
        rows = (
            await db.execute(select(Product.id, Product.name).where(Product.is_active.is_(True)))
        ).all()
        logger.debug(
            "load_entities products: %d rows in %.0fms",
            len(rows), (time.perf_counter() - t0) * 1000,
        )
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    async def _get_all_stores(self, db: AsyncSession) -> list[EntityCandidate]:
        from app.models.stockist import MedicalStore
        t0 = time.perf_counter()
        rows = (
            await db.execute(
                select(MedicalStore.id, MedicalStore.name, MedicalStore.alternate_names).where(
                    MedicalStore.is_active.is_(True)
                )
            )
        ).all()
        candidates: list[EntityCandidate] = []
        for r in rows:
            candidates.append(EntityCandidate(id=str(r[0]), name=r[1]))
            for alt in r[2] or []:
                if alt and str(alt).strip():
                    candidates.append(EntityCandidate(id=str(r[0]), name=str(alt).strip()))
        logger.debug(
            "load_entities stores: %d distinct, %d incl. aliases in %.0fms",
            len(rows), len(candidates), (time.perf_counter() - t0) * 1000,
        )
        return candidates

    async def _get_all_doctors(self, db: AsyncSession) -> list[EntityCandidate]:
        from app.models.doctor import Doctor
        t0 = time.perf_counter()
        rows = (
            await db.execute(
                select(Doctor.id, Doctor.full_name).where(Doctor.is_active.is_(True))
            )
        ).all()
        logger.debug(
            "load_entities doctors: %d rows in %.0fms",
            len(rows), (time.perf_counter() - t0) * 1000,
        )
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    async def _get_all_mrs(self, db: AsyncSession) -> list[EntityCandidate]:
        """Active users with the MR role — for matching an MR/FOS name read
        directly from the file (e.g. a 'TERRITORY'/'Fso name' column)."""
        from app.models.enums import UserRole
        t0 = time.perf_counter()
        rows = (
            await db.execute(
                select(User.id, User.full_name).where(
                    User.is_active.is_(True), User.role == UserRole.MR
                )
            )
        ).all()
        logger.debug(
            "load_entities mrs: %d rows in %.0fms",
            len(rows), (time.perf_counter() - t0) * 1000,
        )
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    # ------------------------------------------------------------------
    # Report-month fallback (fill sale_date for dateless monthly summaries)
    # ------------------------------------------------------------------

    def _apply_report_month(
        self,
        rows: list[dict],
        report_month: str | None,
        filename: str,
        *,
        log_prefix: str = "",
    ) -> tuple[str | None, int]:
        """
        Many distributor reports are monthly summaries with no per-row date —
        only a period in the title ("From 01/01/2026 …") or in the filename
        ("JAN-26"). For any row left without a date, stamp the first day of the
        resolved report month and flag it (`date_estimated`) so the UI can show
        it's an estimate. Returns (month_used 'YYYY-MM' | None, rows_filled).
        """
        month = _resolve_report_month(report_month, filename)
        if not month:
            logger.info(
                "%s apply_report_month: no month determinable (ai=%r file=%r) — "
                "dateless rows will fail validation",
                log_prefix, report_month, filename,
            )
            return None, 0
        first_of_month = f"{month}-01"
        filled = 0
        for r in rows:
            if r.get("sale_date") in (None, "", "null"):
                r["sale_date"] = first_of_month
                r["date_estimated"] = True
                filled += 1
        logger.info(
            "%s apply_report_month: month=%s filled_dateless=%d/%d",
            log_prefix, month, filled, len(rows),
        )
        return month, filled

    # ------------------------------------------------------------------
    # MR resolution (medical store → doctor → active MR allocation)
    # ------------------------------------------------------------------

    async def _resolve_mrs_from_stores(
        self,
        db: AsyncSession,
        rows: list[dict],
        *,
        log_prefix: str = "",
    ) -> list[str]:
        """
        Populate `mr_id` on each row from its `medical_store_id` using:

            doctor_medical_stores  ⨝  mr_doctor_allocations(is_active=true)

        Resolution rules per store:
          • exactly 1 distinct active MR across the store's doctors → set mr_id
          • multiple distinct MRs → leave mr_id None, emit ambiguity warning
          • no doctors / no active MR allocations → leave mr_id None, emit warning

        All store lookups are done in a single batched JOIN — O(1) DB calls.
        Returns a list of human-readable warnings to be surfaced in preview.
        """

        def _pid(val: object) -> uuid.UUID | None:
            if val is None or val == "":
                return None
            try:
                return uuid.UUID(str(val))
            except (ValueError, AttributeError):
                return None

        # Only rows that don't already have an MR from the file need a lookup.
        store_ids: set[uuid.UUID] = set()
        for row in rows:
            if row.get("mr_id"):
                continue
            if s := _pid(row.get("medical_store_id")):
                store_ids.add(s)

        if not store_ids:
            logger.info(
                "%s resolve_mrs_from_stores: no rows need store-based MR lookup "
                "(all have a file-supplied MR, or no store) — skipping",
                log_prefix,
            )
            return []

        t_db = time.perf_counter()
        rs = (
            await db.execute(
                select(
                    DoctorMedicalStore.medical_store_id,
                    MrDoctorAllocation.mr_id,
                )
                .join(
                    MrDoctorAllocation,
                    MrDoctorAllocation.doctor_id == DoctorMedicalStore.doctor_id,
                )
                .where(
                    DoctorMedicalStore.medical_store_id.in_(store_ids),
                    MrDoctorAllocation.is_active.is_(True),
                )
                .distinct()
            )
        ).all()
        logger.info(
            "%s resolve_mrs_from_stores: store_ids=%d join_rows=%d db_ms=%.0f",
            log_prefix, len(store_ids), len(rs),
            (time.perf_counter() - t_db) * 1000,
        )

        store_to_mrs: dict[uuid.UUID, set[uuid.UUID]] = {}
        for sid, mid in rs:
            store_to_mrs.setdefault(sid, set()).add(mid)

        store_to_resolved_mr: dict[uuid.UUID, uuid.UUID] = {}
        ambiguous: set[uuid.UUID] = set()
        unresolved: set[uuid.UUID] = set()

        for sid in store_ids:
            mrs = store_to_mrs.get(sid, set())
            if len(mrs) == 1:
                store_to_resolved_mr[sid] = next(iter(mrs))
            elif len(mrs) > 1:
                ambiguous.add(sid)
            else:
                unresolved.add(sid)

        # Apply resolved mr_ids — but NEVER override an MR already matched from
        # the file (that is authoritative). Only fill rows still missing one.
        for row in rows:
            if row.get("mr_id"):
                continue
            sid = _pid(row.get("medical_store_id"))
            if sid is None:
                row["mr_id"] = None
                continue
            mid = store_to_resolved_mr.get(sid)
            row["mr_id"] = str(mid) if mid is not None else None

        rows_with_mr = sum(1 for r in rows if r.get("mr_id"))
        logger.info(
            "%s resolve_mrs_from_stores: stores resolved_uniquely=%d ambiguous=%d "
            "unresolved=%d → rows_with_mr_id=%d/%d",
            log_prefix, len(store_to_resolved_mr), len(ambiguous), len(unresolved),
            rows_with_mr, len(rows),
        )

        warnings: list[str] = []
        if ambiguous:
            names = await self._get_store_names(db, ambiguous)
            warnings.append(
                f"{len(ambiguous)} medical store(s) have doctors allocated to "
                "multiple MRs — assign mr_id manually in preview: "
                + ", ".join(names[:10])
                + ("..." if len(names) > 10 else "")
            )
        if unresolved:
            names = await self._get_store_names(db, unresolved)
            warnings.append(
                f"{len(unresolved)} medical store(s) have no doctors with active "
                "MR allocations — assign mr_id manually in preview: "
                + ", ".join(names[:10])
                + ("..." if len(names) > 10 else "")
            )
        return warnings

    async def _get_store_names(
        self,
        db: AsyncSession,
        store_ids: set[uuid.UUID],
    ) -> list[str]:
        if not store_ids:
            return []
        rs = (
            await db.execute(
                select(MedicalStore.name)
                .where(MedicalStore.id.in_(store_ids))
                .order_by(MedicalStore.name)
            )
        ).all()
        return [r[0] for r in rs if r[0]]

    async def _hydrate_raw_names_from_db(
        self,
        db: AsyncSession,
        rows: list[dict],
        *,
        log_prefix: str = "",
    ) -> None:
        """
        Preview / commit: show canonical names from DB for any resolved ids.
        If product_id or medical_store_id is missing, clear the corresponding raw name
        so the UI prompts the user to assign an id (sheet text is not kept on null ids).
        """
        from app.models.stockist import MedicalStore

        def _pid(val: object) -> uuid.UUID | None:
            if val is None or val == "":
                return None
            try:
                return uuid.UUID(str(val))
            except (ValueError, AttributeError):
                return None

        product_ids: set[uuid.UUID] = set()
        store_ids: set[uuid.UUID] = set()
        for row in rows:
            if p := _pid(row.get("product_id")):
                product_ids.add(p)
            if s := _pid(row.get("medical_store_id")):
                store_ids.add(s)

        t_db = time.perf_counter()
        prod_map: dict[uuid.UUID, str] = {}
        if product_ids:
            rs = (
                await db.execute(select(Product.id, Product.name).where(Product.id.in_(product_ids)))
            ).all()
            prod_map = {r[0]: (r[1] or "").strip() for r in rs}

        store_map: dict[uuid.UUID, str] = {}
        if store_ids:
            rs = (
                await db.execute(
                    select(MedicalStore.id, MedicalStore.name).where(MedicalStore.id.in_(store_ids))
                )
            ).all()
            store_map = {r[0]: (r[1] or "").strip() for r in rs}
        logger.debug(
            "%s hydrate_raw_names: distinct_products=%d distinct_stores=%d db_ms=%.0f",
            log_prefix, len(product_ids), len(store_ids),
            (time.perf_counter() - t_db) * 1000,
        )

        for row in rows:
            p = _pid(row.get("product_id"))
            if p and p in prod_map and prod_map[p]:
                row["product_name_raw"] = prod_map[p]
            else:
                row["product_name_raw"] = None

            s = _pid(row.get("medical_store_id"))
            if s and s in store_map and store_map[s]:
                row["customer_name_raw"] = store_map[s]
            else:
                row["customer_name_raw"] = None

    # ------------------------------------------------------------------
    # Auto-populate derived fields (division/location/HQ/state + doctor)
    # ------------------------------------------------------------------

    async def _enrich_derived_fields(
        self,
        db: AsyncSession,
        rows: list[dict],
        *,
        log_prefix: str = "",
    ) -> None:
        """
        Auto-populate derived fields on each row using batch DB lookups:

          division_id    <- product.division_id
          headquarter_id <- medical_store.headquarter_id (fallback: doctor.headquarter_id)
          state_id       <- headquarter.state_id
          location_id    <- None (sale.location_id is nullable; sub-location is
                                  no longer derived from store/doctor)

        Also auto-fills `doctor_id` when null and (mr_id, medical_store_id) are
        known: the unique doctor that is both linked to the store
        (`doctor_medical_stores`) AND allocated to the MR (`mr_doctor_allocations`).
        """

        def _pid(val: object) -> uuid.UUID | None:
            if val is None or val == "":
                return None
            try:
                return uuid.UUID(str(val))
            except (ValueError, AttributeError):
                return None

        def _to_float(val: object) -> float | None:
            if val is None or val == "":
                return None
            try:
                return float(str(val).replace(",", "").strip())
            except (ValueError, TypeError):
                return None

        # ---------- Pass 1: collect ids ----------
        product_ids: set[uuid.UUID] = set()
        store_ids: set[uuid.UUID] = set()
        doctor_ids: set[uuid.UUID] = set()

        for row in rows:
            if p := _pid(row.get("product_id")):
                product_ids.add(p)
            if s := _pid(row.get("medical_store_id")):
                store_ids.add(s)
            if d := _pid(row.get("doctor_id")):
                doctor_ids.add(d)

        # ---------- Step A: doctor auto-fill (mr_id + store_id -> doctor_id) ----------
        # Build the (mr_id, store_id) pairs that need a doctor assignment.
        autofill_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
        autofill_pair_set: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for row in rows:
            if _pid(row.get("doctor_id")) is not None:
                continue
            mr = _pid(row.get("mr_id"))
            st = _pid(row.get("medical_store_id"))
            if mr is None or st is None:
                continue
            pair = (mr, st)
            if pair not in autofill_pair_set:
                autofill_pair_set.add(pair)
                autofill_pairs.append(pair)

        pair_to_doctor: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID] = {}
        if autofill_pairs:
            store_set = {p[1] for p in autofill_pairs}
            mr_set = {p[0] for p in autofill_pairs}

            store_doc_rows = (
                await db.execute(
                    select(
                        DoctorMedicalStore.medical_store_id,
                        DoctorMedicalStore.doctor_id,
                    ).where(DoctorMedicalStore.medical_store_id.in_(store_set))
                )
            ).all()
            store_doctor_map: dict[uuid.UUID, set[uuid.UUID]] = {}
            for sid, did in store_doc_rows:
                store_doctor_map.setdefault(sid, set()).add(did)

            mr_alloc_rows = (
                await db.execute(
                    select(MrDoctorAllocation.mr_id, MrDoctorAllocation.doctor_id).where(
                        MrDoctorAllocation.mr_id.in_(mr_set),
                        MrDoctorAllocation.is_active.is_(True),
                    )
                )
            ).all()
            mr_doctor_map: dict[uuid.UUID, set[uuid.UUID]] = {}
            for mid, did in mr_alloc_rows:
                mr_doctor_map.setdefault(mid, set()).add(did)

            for mr, st in autofill_pairs:
                store_docs = store_doctor_map.get(st, set())
                mr_docs = mr_doctor_map.get(mr, set())
                common = store_docs & mr_docs
                if len(common) == 1:
                    pair_to_doctor[(mr, st)] = next(iter(common))

        # Apply auto-filled doctor_ids and add them to the doctor_id batch lookup.
        autofilled_doctor_count = 0
        for row in rows:
            if _pid(row.get("doctor_id")) is not None:
                continue
            mr = _pid(row.get("mr_id"))
            st = _pid(row.get("medical_store_id"))
            if mr is None or st is None:
                continue
            did = pair_to_doctor.get((mr, st))
            if did is not None:
                row["doctor_id"] = str(did)
                doctor_ids.add(did)
                autofilled_doctor_count += 1

        # ---------- Step B: batch fetch products / stores / doctors ----------
        product_map: dict[uuid.UUID, uuid.UUID] = {}  # product_id -> division_id
        product_meta: dict[uuid.UUID, tuple] = {}     # product_id -> (ptr, mrp, pack_size)
        if product_ids:
            rs = (
                await db.execute(
                    select(
                        Product.id, Product.division_id,
                        Product.ptr, Product.mrp, Product.pack_size,
                    ).where(Product.id.in_(product_ids))
                )
            ).all()
            product_map = {r[0]: r[1] for r in rs}
            product_meta = {r[0]: (r[2], r[3], r[4]) for r in rs}

        store_map: dict[uuid.UUID, uuid.UUID | None] = {}  # store_id -> headquarter_id
        if store_ids:
            rs = (
                await db.execute(
                    select(MedicalStore.id, MedicalStore.headquarter_id).where(
                        MedicalStore.id.in_(store_ids)
                    )
                )
            ).all()
            store_map = {r[0]: r[1] for r in rs}

        doctor_map: dict[uuid.UUID, uuid.UUID | None] = {}  # doctor_id -> headquarter_id
        if doctor_ids:
            rs = (
                await db.execute(
                    select(Doctor.id, Doctor.headquarter_id).where(Doctor.id.in_(doctor_ids))
                )
            ).all()
            doctor_map = {r[0]: r[1] for r in rs}

        # ---------- Step C: fetch headquarter -> state_id ----------
        hq_ids: set[uuid.UUID] = set()
        for v in store_map.values():
            if v is not None:
                hq_ids.add(v)
        for v in doctor_map.values():
            if v is not None:
                hq_ids.add(v)

        hq_map: dict[uuid.UUID, uuid.UUID] = {}  # hq_id -> state_id
        if hq_ids:
            rs = (
                await db.execute(
                    select(Headquarter.id, Headquarter.state_id).where(Headquarter.id.in_(hq_ids))
                )
            ).all()
            hq_map = {r[0]: r[1] for r in rs}

        # ---------- Step D: per-row derivation ----------
        div_filled = hq_filled = state_filled = 0
        for row in rows:
            pid = _pid(row.get("product_id"))
            sid = _pid(row.get("medical_store_id"))
            did = _pid(row.get("doctor_id"))

            division_id = product_map.get(pid) if pid else None

            hq_id: uuid.UUID | None = None
            if sid is not None:
                hq_id = store_map.get(sid)
            if hq_id is None and did is not None:
                hq_id = doctor_map.get(did)

            state_id = hq_map.get(hq_id) if hq_id else None

            row["division_id"] = str(division_id) if division_id else None
            # location_id is no longer derived from store/doctor; sale rows
            # carry it as nullable. Set explicitly to None so downstream code
            # that reads the field never sees stale data.
            row["location_id"] = None
            row["headquarter_id"] = str(hq_id) if hq_id else None
            row["state_id"] = str(state_id) if state_id else None

            # ---- runtime-computed values + master fallbacks (not stored cols) ----
            meta = product_meta.get(pid) if pid else None
            rate = _to_float(row.get("ptr"))
            if rate is None and meta is not None:
                rate = _to_float(meta[0])  # product.ptr
            sale_qty = _to_float(row.get("sale_qty")) or 0.0
            free_qty = _to_float(row.get("free_qty")) or 0.0
            reported = _to_float(row.get("reported_amount"))
            free_val_raw = _to_float(row.get("free_value_raw"))

            row["sale_value"] = (
                reported if reported is not None
                else (round(sale_qty * rate, 2) if rate is not None else None)
            )
            row["free_value"] = (
                free_val_raw if free_val_raw is not None
                else (round(free_qty * rate, 2) if rate is not None else None)
            )
            row["total_amount"] = round(sale_qty * rate, 2) if rate is not None else None

            # Fill display rate / mrp / pack from master when the file omitted them.
            if not row.get("ptr") and meta is not None:
                row["ptr"] = _to_float(meta[0])
            if not row.get("mrp") and meta is not None:
                row["mrp"] = _to_float(meta[1])
            if not row.get("pack") and meta is not None and meta[2]:
                row["pack"] = meta[2]

            if division_id:
                div_filled += 1
            if hq_id:
                hq_filled += 1
            if state_id:
                state_filled += 1

        n = len(rows)
        logger.info(
            "%s enrich_derived: rows=%d auto_doctor=%d division=%d/%d "
            "hq=%d/%d state=%d/%d",
            log_prefix, n, autofilled_doctor_count,
            div_filled, n, hq_filled, n, state_filled, n,
        )

    # ------------------------------------------------------------------
    # Allocation helpers
    # ------------------------------------------------------------------

    async def _resolve_mr_headquarter(
        self,
        db: AsyncSession,
        mr_id: uuid.UUID,
        product_id: uuid.UUID,
    ) -> tuple[uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]:
        """Return (state_id, headquarter_id, division_id) for an MR + product.

        Picks the first active MR-headquarter allocation whose HQ serves the
        product's division. Returns all-None if the MR has no qualifying
        allocation.
        """
        product = await db.get(Product, product_id)
        if product is None:
            return None, None, None

        stmt = (
            select(MrHeadquarterAllocation.headquarter_id, Headquarter.state_id)
            .join(Headquarter, MrHeadquarterAllocation.headquarter_id == Headquarter.id)
            .where(
                MrHeadquarterAllocation.mr_id == mr_id,
                MrHeadquarterAllocation.is_active.is_(True),
                Headquarter.division_ids.contains([product.division_id]),
            )
            .limit(1)
        )
        row = (await db.execute(stmt)).one_or_none()
        if row is None:
            return None, None, None

        hq_id, state_id = row
        return state_id, hq_id, product.division_id

    async def _explain_allocation_failure(
        self,
        db: AsyncSession,
        mr_id: uuid.UUID,
        product_id: uuid.UUID,
    ) -> str:
        from app.models.master import Division

        mr_row = (await db.execute(select(User.full_name).where(User.id == mr_id))).one_or_none()
        mr_name = mr_row[0] if mr_row else str(mr_id)

        product = await db.get(Product, product_id)
        product_name = product.name if product else str(product_id)
        product_div_name: str | None = None
        if product is not None and product.division_id is not None:
            dr = (
                await db.execute(select(Division.name).where(Division.id == product.division_id))
            ).one_or_none()
            product_div_name = dr[0] if dr else str(product.division_id)

        alloc_rows = (
            await db.execute(
                select(MrHeadquarterAllocation.id)
                .where(MrHeadquarterAllocation.mr_id == mr_id)
                .where(MrHeadquarterAllocation.is_active.is_(True))
            )
        ).all()

        if not alloc_rows:
            return (
                f"MR '{mr_name}' has no active headquarter allocations. "
                f"Create one in mr_headquarter_allocations before committing."
            )

        return (
            f"MR '{mr_name}' has {len(alloc_rows)} active headquarter allocation(s), "
            f"but none cover division '{product_div_name or 'unknown'}' "
            f"(product: {product_name}). Either assign the MR to an HQ that "
            f"includes this division, or add this division to one of the MR's current HQs."
        )

    async def _find_mrs_without_allocations(
        self,
        db: AsyncSession,
        mr_ids: set[uuid.UUID],
    ) -> set[uuid.UUID]:
        if not mr_ids:
            return set()
        rows = (
            await db.execute(
                select(MrHeadquarterAllocation.mr_id)
                .where(MrHeadquarterAllocation.mr_id.in_(mr_ids))
                .where(MrHeadquarterAllocation.is_active.is_(True))
                .distinct()
            )
        ).all()
        allocated = {r[0] for r in rows}
        return mr_ids - allocated

    async def _get_user_names(
        self,
        db: AsyncSession,
        user_ids: set[uuid.UUID],
    ) -> list[str]:
        if not user_ids:
            return []
        rows = (
            await db.execute(
                select(User.full_name).where(User.id.in_(user_ids)).order_by(User.full_name)
            )
        ).all()
        return [r[0] for r in rows if r[0]]


# ---------------------------------------------------------------------------
# Normalisation helper for fuzzy matching
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Upper-case, strip non-alphanumeric, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", s.upper())).strip()


_MONTH_NAMES = {
    "JAN": 1, "FEB": 2, "MAR": 3, "MARCH": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _resolve_report_month(ai_month: str | None, filename: str) -> str | None:
    """
    Decide the report month as 'YYYY-MM'. Prefer the month the LLM read from the
    file's title/header; fall back to the filename (e.g. 'JAN-26', 'MARCH 26').
    Returns None if neither yields a month.
    """
    if ai_month:
        m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})\s*", str(ai_month))
        if m and 1 <= int(m.group(2)) <= 12:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"

    up = filename.upper()
    name_hit = re.search(r"\b(JAN|FEB|MARCH|MAR|APR|MAY|JUN|JUL|AUG|SEPT|SEP|OCT|NOV|DEC)\b", up)
    year_hit = re.search(r"\b(20\d{2}|\d{2})\b", up.split(name_hit.group(1), 1)[1]) if name_hit else None
    if name_hit:
        month = _MONTH_NAMES[name_hit.group(1)]
        year = 2000 + int(year_hit.group(1)) if (year_hit and len(year_hit.group(1)) == 2) \
            else (int(year_hit.group(1)) if year_hit else None)
        if year:
            return f"{year:04d}-{month:02d}"
    return None
