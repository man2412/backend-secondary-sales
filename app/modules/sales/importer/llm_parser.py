"""
LLM-based parser: takes raw extracted text + entity candidate lists,
returns structured sale rows with resolved IDs.

Primary: Gemini 2.0 Flash
Fallback: GPT-4o-mini
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EntityCandidate:
    id: str
    name: str


@dataclass
class LLMParseRequest:
    raw_text: str
    products: list[EntityCandidate]
    medical_stores: list[EntityCandidate]
    mrs: list[EntityCandidate]
    doctors: list[EntityCandidate]
    detected_fos_name: str | None = None


@dataclass
class LLMParseResponse:
    rows: list[dict]
    model_used: str
    raw_response: dict


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are a data extraction assistant for a pharmaceutical CRM. \
Extract every secondary sale row from the provided distributor sales report text.

OUTPUT FORMAT: Return a single JSON object with a key "rows" containing an array. \
Each element represents one sale transaction with these fields:

Required:
  product_id       — UUID from the products list below (match by name similarity); null if no match
  product_name_raw — exact product name string from the file
  sale_date        — ISO date string YYYY-MM-DD; null if not present
  sale_qty         — integer quantity sold; null if not present

Optional (include if present in the data, else omit or set null):
  free_qty         — integer free quantity
  mrp              — decimal MRP value
  ptr              — decimal rate/selling price (Rate column maps to ptr)
  reported_amount  — decimal total amount as reported by distributor (Amount or Value column)
  bill_ref         — bill/invoice reference string
  batch            — batch number string
  pack             — pack size/form string (e.g. "1X10TA")
  medical_store_id — UUID from the medical stores list below (match by name); null if no match
  customer_name_raw— exact customer/party name from file
  doctor_id        — UUID from the doctors list below (match by name); null if no match or not present
  doctor_name_raw  — exact doctor name from file
  mr_id            — UUID from the MRs list below (match FOS name); null if not in this row
  mr_name_raw      — exact FOS/MR name from file (only for multi-MR files)
  confidence       — "high" | "medium" | "low" (your confidence in this row's extraction)

RULES:
- Skip subtotal rows ("Party Total", "Grand Total", "Customer Total", totals).
- Skip page headers repeated mid-file.
- If the file has customer section headers (lines that introduce a group of rows for one customer), \
  inject that customer name into every row in that section as customer_name_raw.
- Date formats vary; parse them all to YYYY-MM-DD.
- Numbers may use commas as thousand separators; parse as decimals.
- If a product name partially matches (abbreviation, spacing difference), still resolve to the closest product_id.
- If no match found for a name, set the ID field to null but still include the raw name field.
- Do NOT invent data. Only extract what is present in the text.

PRODUCTS (id → name):
{products}

MEDICAL STORES (id → name):
{stores}

MRS / FOS (id → name):
{mrs}

DOCTORS (id → name):
{doctors}
"""

def _format_entity_list(entities: list[EntityCandidate]) -> str:
    if not entities:
        return "(none)"
    return "\n".join(f"  {e.id}: {e.name}" for e in entities)


def _build_prompt(req: LLMParseRequest) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""
    system = _SYSTEM_PROMPT_TEMPLATE.format(
        products=_format_entity_list(req.products),
        stores=_format_entity_list(req.medical_stores),
        mrs=_format_entity_list(req.mrs),
        doctors=_format_entity_list(req.doctors),
    )
    user = f"Extract all sale rows from the following distributor report:\n\n{req.raw_text}"
    return system, user


# ---------------------------------------------------------------------------
# Gemini (primary) — with retry on transient failures
# ---------------------------------------------------------------------------

_GEMINI_MODEL = "gemini-1.5-flash"
_GEMINI_TIMEOUT_S = 180
_GEMINI_MAX_ATTEMPTS = 3
_GEMINI_BACKOFF_SECONDS = (1, 4, 16)


def _is_transient_gemini_error(exc: BaseException) -> bool:
    """Retry on quota / timeout / availability; don't retry on schema / auth errors."""
    name = type(exc).__name__
    return name in {
        "ResourceExhausted",      # 429
        "DeadlineExceeded",       # 504
        "ServiceUnavailable",     # 503
        "InternalServerError",    # 500
        "Aborted",                # transient
        "Unknown",                # network blip
        "JSONDecodeError",        # truncated / malformed JSON — worth retrying
    }


def _call_gemini(system: str, user: str) -> tuple[dict, str]:
    import time
    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=_GEMINI_MODEL,
        system_instruction=system,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.0,
            max_output_tokens=8192,
        ),
    )

    last_exc: Exception | None = None
    for attempt in range(_GEMINI_MAX_ATTEMPTS):
        try:
            response = model.generate_content(
                user,
                request_options={"timeout": _GEMINI_TIMEOUT_S},
            )
            return json.loads(response.text), _GEMINI_MODEL
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt + 1 < _GEMINI_MAX_ATTEMPTS and _is_transient_gemini_error(exc):
                delay = _GEMINI_BACKOFF_SECONDS[attempt]
                logger.warning(
                    "Gemini attempt %d/%d failed (%s), retrying in %ds",
                    attempt + 1, _GEMINI_MAX_ATTEMPTS, type(exc).__name__, delay,
                )
                time.sleep(delay)
                continue
            break

    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# OpenAI (fallback)
# ---------------------------------------------------------------------------

def _call_openai(system: str, user: str) -> tuple[dict, str]:
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = json.loads(response.choices[0].message.content or "{}")
    return raw, "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_with_llm(req: LLMParseRequest) -> LLMParseResponse:
    """Call LLM and return structured rows. Raises RuntimeError if both models fail."""
    system, user = _build_prompt(req)

    raw: dict = {}
    model_used = ""

    _gemini_error: Exception | None = None
    if settings.GEMINI_API_KEY:
        try:
            raw, model_used = _call_gemini(system, user)
        except Exception as exc:
            _gemini_error = exc
            logger.warning("Gemini failed (%s), trying OpenAI fallback", exc)

    if not model_used and settings.OPENAI_API_KEY:
        try:
            raw, model_used = _call_openai(system, user)
        except Exception as exc:
            logger.error("OpenAI fallback also failed: %s", exc)
            raise RuntimeError("Both LLM providers failed. Check API keys and connectivity.") from exc

    if not model_used:
        if not settings.GEMINI_API_KEY and not settings.OPENAI_API_KEY:
            raise RuntimeError("No LLM API keys configured. Set GEMINI_API_KEY or OPENAI_API_KEY in .env")
        if _gemini_error is not None:
            raise RuntimeError(
                f"Gemini call failed: {type(_gemini_error).__name__}: {_gemini_error}"
            ) from _gemini_error
        raise RuntimeError("LLM call returned no model_used (unknown reason)")

    rows: list[dict] = raw.get("rows", [])
    if not isinstance(rows, list):
        rows = []

    return LLMParseResponse(rows=rows, model_used=model_used, raw_response=raw)
