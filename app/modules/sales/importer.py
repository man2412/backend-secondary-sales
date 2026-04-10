import io
import uuid
from datetime import date

import openpyxl
import pdfplumber


def _parse_uuid(v: object | None) -> uuid.UUID | None:
    if v is None:
        return None
    if isinstance(v, uuid.UUID):
        return v
    s = str(v).strip()
    if not s:
        return None
    return uuid.UUID(s)


def _parse_date(v: object) -> date:
    if isinstance(v, date):
        return v
    s = str(v).strip()
    return date.fromisoformat(s[:10])


def _parse_int(v: object | None, default: int = 0) -> int:
    if v is None or str(v).strip() == "":
        return default
    return int(float(v))


def _parse_float(v: object | None) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    return float(v)


def parse_secondary_sales_xlsx(content: bytes) -> list[dict]:
    """
    Expected columns (case-insensitive, underscores/spaces ok):
      - mr_id (required for SUPER_ADMIN import)
      - product_id (required)
      - location_id (required)
      - sale_date (required, YYYY-MM-DD)
      - sale_qty (required)
      - free_qty (optional)
      - doctor_id (optional)
      - medical_store_id (optional)
      - special_price (optional)
      - remarks (optional)
    """
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(x).strip() if x is not None else "" for x in rows[0]]

    def norm(x: str) -> str:
        return x.strip().lower().replace(" ", "_")

    idx = {norm(h): i for i, h in enumerate(header) if h}
    out: list[dict] = []
    for r in rows[1:]:
        if r is None or all(v is None or str(v).strip() == "" for v in r):
            continue
        get = lambda k: r[idx[k]] if k in idx and idx[k] < len(r) else None
        out.append(
            {
                "mr_id": _parse_uuid(get("mr_id")),
                "product_id": _parse_uuid(get("product_id")),
                "location_id": _parse_uuid(get("location_id")),
                "sale_date": _parse_date(get("sale_date")),
                "sale_qty": _parse_int(get("sale_qty")),
                "free_qty": _parse_int(get("free_qty"), 0),
                "doctor_id": _parse_uuid(get("doctor_id")),
                "medical_store_id": _parse_uuid(get("medical_store_id")),
                "special_price": _parse_float(get("special_price")),
                "remarks": (str(get("remarks")).strip() if get("remarks") is not None else None),
            }
        )
    return out


def parse_secondary_sales_pdf(content: bytes) -> list[dict]:
    """
    Best-effort PDF parsing.
    Supported only for PDFs that contain a table with the same headers as the XLSX importer.
    """
    out: list[dict] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table or len(table) < 2:
                continue
            header = [str(x).strip() if x is not None else "" for x in table[0]]

            def norm(x: str) -> str:
                return x.strip().lower().replace(" ", "_")

            idx = {norm(h): i for i, h in enumerate(header) if h}
            for r in table[1:]:
                if r is None or all(v is None or str(v).strip() == "" for v in r):
                    continue
                get = lambda k: r[idx[k]] if k in idx and idx[k] < len(r) else None
                out.append(
                    {
                        "mr_id": _parse_uuid(get("mr_id")),
                        "product_id": _parse_uuid(get("product_id")),
                        "location_id": _parse_uuid(get("location_id")),
                        "sale_date": _parse_date(get("sale_date")),
                        "sale_qty": _parse_int(get("sale_qty")),
                        "free_qty": _parse_int(get("free_qty"), 0),
                        "doctor_id": _parse_uuid(get("doctor_id")),
                        "medical_store_id": _parse_uuid(get("medical_store_id")),
                        "special_price": _parse_float(get("special_price")),
                        "remarks": (str(get("remarks")).strip() if get("remarks") is not None else None),
                    }
                )
    return out

