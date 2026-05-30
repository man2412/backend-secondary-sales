"""
Debug helper #2 — summarize the data-side problem causing the importer
to report "N medical store(s) have doctors allocated to multiple MRs".

We focus on the 99 ambiguous stores from the MEXON PHARMA CD CARE upload
by reading the CSV mapping the user already produced.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

CSV_MAPPING = PROJECT_ROOT / "medical_store_id_mappings.csv"
NEW_DOCTOR = "283382d2-c4be-4bbc-875a-84ba1eae535c"  # CD CARE Rajkot General
LEGACY_DOCTOR = "5c6e4015-886d-4ebe-a9f0-55a1559cabed"  # CD CARE GENERAL RAJKOT-1


def _pg_dsn() -> str:
    return re.sub(r"^postgresql\+[^:]+://", "postgresql://", os.environ["DATABASE_URL"])


def main() -> None:
    df = pd.read_csv(CSV_MAPPING)
    store_ids = df["medical_store_id"].dropna().astype(str).unique().tolist()

    with psycopg2.connect(_pg_dsn()) as conn, conn.cursor() as cur:
        # ---- 1) Reproduce importer logic exactly: store -> distinct active MRs.
        cur.execute(
            """
            SELECT dms.medical_store_id, mda.mr_id
            FROM doctor_medical_stores dms
            JOIN mr_doctor_allocations mda
              ON mda.doctor_id = dms.doctor_id
             AND mda.is_active = true
            WHERE dms.medical_store_id = ANY(%s::uuid[])
            """,
            (store_ids,),
        )
        store_to_mrs: dict[str, set[str]] = {}
        for sid, mid in cur.fetchall():
            store_to_mrs.setdefault(str(sid), set()).add(str(mid))

        resolved = sum(1 for v in store_to_mrs.values() if len(v) == 1)
        ambiguous = [sid for sid, v in store_to_mrs.items() if len(v) > 1]
        unresolved = [sid for sid in store_ids if sid not in store_to_mrs]

        print(f"CSV stores total              : {len(store_ids)}")
        print(f"  -> resolved (1 active MR)   : {resolved}")
        print(f"  -> ambiguous (>1 active MR) : {len(ambiguous)}")
        print(f"  -> unresolved (0 active MR) : {len(unresolved)}")

        # ---- 2) WHY ambiguous? It's almost always because some doctor that
        # the store is linked to has MORE THAN ONE active MR allocation.
        # Confirm by listing all doctors involved in the ambiguous stores
        # and how many active MRs each one has.
        cur.execute(
            """
            SELECT dms.doctor_id, d.full_name, count(DISTINCT mda.mr_id) AS active_mrs
            FROM doctor_medical_stores dms
            JOIN doctors d ON d.id = dms.doctor_id
            LEFT JOIN mr_doctor_allocations mda
              ON mda.doctor_id = dms.doctor_id AND mda.is_active = true
            WHERE dms.medical_store_id = ANY(%s::uuid[])
            GROUP BY dms.doctor_id, d.full_name
            ORDER BY active_mrs DESC, d.full_name
            """,
            (ambiguous or ["00000000-0000-0000-0000-000000000000"],),
        )
        print("\nDoctors linked to ambiguous stores (and their active-MR count):")
        for did, dname, cnt in cur.fetchall():
            print(f"  {cnt} active MR(s)  doctor={did}  {dname}")

        # ---- 3) Concretely list the active MRs on the offending doctor.
        cur.execute(
            """
            SELECT mda.mr_id, u.full_name, mda.allocated_at, mda.is_active
            FROM mr_doctor_allocations mda
            LEFT JOIN users u ON u.id = mda.mr_id
            WHERE mda.doctor_id = %s
            ORDER BY mda.is_active DESC, mda.allocated_at
            """,
            (LEGACY_DOCTOR,),
        )
        print(f"\nAll MR allocations for legacy doctor {LEGACY_DOCTOR} "
              f"(CD CARE GENERAL RAJKOT-1):")
        for mid, mname, at, act in cur.fetchall():
            print(f"  is_active={str(act):5s}  allocated_at={at}  mr={mid}  {mname}")

        cur.execute(
            """
            SELECT mda.mr_id, u.full_name, mda.allocated_at, mda.is_active
            FROM mr_doctor_allocations mda
            LEFT JOIN users u ON u.id = mda.mr_id
            WHERE mda.doctor_id = %s
            ORDER BY mda.is_active DESC, mda.allocated_at
            """,
            (NEW_DOCTOR,),
        )
        print(f"\nAll MR allocations for NEW doctor {NEW_DOCTOR} "
              f"(CD CARE Rajkot General — added by new_script.py):")
        rows = cur.fetchall()
        if not rows:
            print("  (no rows — this doctor has NO mr_doctor_allocations entry at all)")
        for mid, mname, at, act in rows:
            print(f"  is_active={str(act):5s}  allocated_at={at}  mr={mid}  {mname}")

        # ---- 4) How many doctors in the WHOLE DB currently have >1 active MR?
        cur.execute(
            """
            SELECT count(*) FROM (
              SELECT doctor_id
              FROM mr_doctor_allocations
              WHERE is_active = true
              GROUP BY doctor_id
              HAVING count(DISTINCT mr_id) > 1
            ) t
            """,
        )
        print(f"\nDB-wide: {cur.fetchone()[0]} doctor(s) currently have >1 active MR allocation.")


if __name__ == "__main__":
    main()
