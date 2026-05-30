"""
Diagnose the 10 stores still listed as UNRESOLVED after the latest fix.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

UNRESOLVED_NAMES = [
    "ASHIRWAD MEDICINES, VAKANER, VAKANER",
    "DOSHI BRODHERS, VAKANER, VAKANER",
    "HEALTH CARE MEDI.STORE, VAKANER, VAKANER",
    "ME. GORDHANDAS BHANJIBHAI, DHROL, DHROL",
    "PARESH CHEMIST PADADHARI, PADADHARI, PADADHARI",
    "POOJA MEDICAL, VAKANER, VAKANER",
    "RAJSHAKTI MEDICAL, VAKANER, VAKANER",
    "RAJSHAKTI MEDICINES, VAKANER, VAKANER",
    "SAHAKAR MEDICAL STORE, VAKANER, VAKANER",
    "SHIVAM MEDICAL STORE, VAKANER, VAKANER",
]


def _pg_dsn() -> str:
    return re.sub(r"^postgresql\+[^:]+://", "postgresql://", os.environ["DATABASE_URL"])


def banner(s: str) -> None:
    print("\n" + "=" * 100)
    print(s)
    print("=" * 100)


def main() -> None:
    with psycopg2.connect(_pg_dsn()) as conn, conn.cursor() as cur:
        # 1) Resolve each store name to its id (exact, case-insensitive match).
        banner("1) RESOLVE STORE IDS (exact case-insensitive name match)")
        store_rows: list[tuple[str, str, str | None]] = []  # (id, name, hq_id)
        for nm in UNRESOLVED_NAMES:
            cur.execute(
                """
                SELECT id, name, headquarter_id
                FROM medical_stores
                WHERE LOWER(name) = LOWER(%s)
                """,
                (nm,),
            )
            rows = cur.fetchall()
            if not rows:
                print(f"  NOT FOUND : {nm}")
                continue
            for sid, name, hq in rows:
                print(f"  {sid}  {name}")
                store_rows.append((str(sid), name, str(hq) if hq else None))

        if not store_rows:
            print("\nNo stores resolved; aborting.")
            return

        store_ids = [s[0] for s in store_rows]

        # 2) For each store: linked doctors, each doctor's active+inactive MRs.
        banner("2) PER-STORE LINKED DOCTORS + THEIR MR ALLOCATIONS")
        for sid, name, hq in store_rows:
            print(f"\n  store {sid}  {name}")
            if hq:
                cur.execute("SELECT name FROM headquarters WHERE id = %s::uuid", (hq,))
                hq_name = (cur.fetchone() or ["?"])[0]
                print(f"    headquarter: {hq_name}  ({hq})")

            cur.execute(
                """
                SELECT d.id, d.full_name, d.is_active, d.headquarter_id
                FROM doctor_medical_stores dms
                JOIN doctors d ON d.id = dms.doctor_id
                WHERE dms.medical_store_id = %s::uuid
                ORDER BY d.full_name
                """,
                (sid,),
            )
            doctors = cur.fetchall()
            if not doctors:
                print("    NO doctors linked to this store at all.")
                continue
            for did, dname, dact, dhq in doctors:
                print(f"    doctor {did}  {dname}  is_active={dact}  hq={dhq}")
                cur.execute(
                    """
                    SELECT mda.mr_id, u.full_name, mda.is_active, mda.allocated_at
                    FROM mr_doctor_allocations mda
                    LEFT JOIN users u ON u.id = mda.mr_id
                    WHERE mda.doctor_id = %s
                    ORDER BY mda.is_active DESC, mda.allocated_at
                    """,
                    (did,),
                )
                allocs = cur.fetchall()
                if not allocs:
                    print("      (no mr_doctor_allocations rows)")
                for mid, mname, act, at in allocs:
                    tag = "ACTIVE" if act else "inactive"
                    print(f"      [{tag:8s}] mr={mid}  {mname}  allocated_at={at}")

        # 3) Compact per-store verdict.
        banner("3) PER-STORE VERDICT")
        cur.execute(
            """
            SELECT s.id, s.name,
                   COUNT(DISTINCT dms.doctor_id)                              AS n_doctors,
                   COUNT(DISTINCT mda.mr_id) FILTER (WHERE mda.is_active)     AS n_active_mrs
            FROM medical_stores s
            LEFT JOIN doctor_medical_stores dms ON dms.medical_store_id = s.id
            LEFT JOIN mr_doctor_allocations mda ON mda.doctor_id = dms.doctor_id
            WHERE s.id = ANY(%s::uuid[])
            GROUP BY s.id, s.name
            ORDER BY s.name
            """,
            (store_ids,),
        )
        for sid, name, nd, na in cur.fetchall():
            if na == 1:
                verdict = "RESOLVED"
            elif na > 1:
                verdict = "AMBIGUOUS"
            elif nd == 0:
                verdict = "UNRESOLVED (no doctor linked)"
            else:
                verdict = "UNRESOLVED (linked doctor has no active MR)"
            print(f"  doctors={nd}  active_mrs={na}  {verdict:55s}  {name}")


if __name__ == "__main__":
    main()
