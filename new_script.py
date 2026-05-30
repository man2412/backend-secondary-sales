import os
import re
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

CSV_PATH = "/Users/kushalchadmiya/Downloads/cd_care_rajkot_general_medical_stores.csv"
OUTPUT_PATH = PROJECT_ROOT / "medical_store_id_mappings.csv"
# `CD CARE Rajkot General` — link every matched store from CSV_PATH to this doctor.
DOCTOR_ID = "283382d2-c4be-4bbc-875a-84ba1eae535c"


def _build_pg_dsn() -> str:
    raw = os.getenv("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL is not set in .env")
    # `postgresql+asyncpg://...` is the SQLAlchemy form; psycopg expects plain `postgresql://`.
    return re.sub(r"^postgresql\+[^:]+://", "postgresql://", raw)


def normalize_name(name: str) -> str:
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


df = pd.read_csv(CSV_PATH)

store_names = (
    df["CHEMIST NAME"]
    .dropna()
    .astype(str)
    .unique()
    .tolist()
)

normalized_input = {normalize_name(name): name for name in store_names}

all_medical_stores: list[dict] = []

with psycopg2.connect(_build_pg_dsn()) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, alternate_names FROM medical_stores")
        for row_id, row_name, row_alts in cur:
            all_medical_stores.append(
                {"id": row_id, "name": row_name, "alternate_names": row_alts}
            )

    print(f"Fetched {len(all_medical_stores)} medical stores from DB")

    lookup: dict[str, str] = {}

    for store in all_medical_stores:
        store_id = store["id"]

        primary_name = store.get("name")
        if primary_name:
            lookup[normalize_name(primary_name)] = store_id

        for alt in store.get("alternate_names") or []:
            normalized_alt = normalize_name(alt)
            if normalized_alt:
                lookup[normalized_alt] = store_id

    results = []
    for normalized, original in normalized_input.items():
        results.append(
            {
                "input_name": original,
                "normalized_name": normalized,
                "medical_store_id": lookup.get(normalized),
            }
        )

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUTPUT_PATH, index=False)

    matched = results_df["medical_store_id"].notnull().sum()
    print(f"Matched: {matched}/{len(results_df)}")
    print(f"Saved: {OUTPUT_PATH}")

    # ---- Link matched stores to the target doctor ----
    matched_ids = sorted(
        {row["medical_store_id"] for row in results if row["medical_store_id"]}
    )

    if not matched_ids:
        print("No matched store IDs — skipping doctor link step.")
    else:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT full_name FROM doctors WHERE id = %s", (DOCTOR_ID,)
            )
            doctor_row = cur.fetchone()
            if not doctor_row:
                raise RuntimeError(f"Doctor {DOCTOR_ID} not found")
            doctor_name = doctor_row[0]

            cur.execute(
                "SELECT count(*) FROM doctor_medical_stores WHERE doctor_id = %s",
                (DOCTOR_ID,),
            )
            before = cur.fetchone()[0]

            execute_values(
                cur,
                """
                INSERT INTO doctor_medical_stores (doctor_id, medical_store_id)
                VALUES %s
                ON CONFLICT DO NOTHING
                """,
                [(DOCTOR_ID, sid) for sid in matched_ids],
                # Single statement so rowcount reflects all inserts (not just the last chunk).
                page_size=max(len(matched_ids), 1),
            )
            inserted = cur.rowcount

            cur.execute(
                "SELECT count(*) FROM doctor_medical_stores WHERE doctor_id = %s",
                (DOCTOR_ID,),
            )
            after = cur.fetchone()[0]

        conn.commit()
        print(
            f"Linked to doctor '{doctor_name}' ({DOCTOR_ID}): "
            f"inserted {inserted} new of {len(matched_ids)} matched "
            f"(before={before}, after={after})"
        )
