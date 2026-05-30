"""
Full forensic report on a single store:
  AASHNA M.S. gauridal pedak, RAJKOT, RAJKOT
  id = 45819330-fb1b-4f3b-b1f6-406dd52f793c

Walks the exact same chain the importer uses to resolve mr_id from a
medical_store_id, prints every intermediate row, and ends with a
verdict (RESOLVED / AMBIGUOUS / UNRESOLVED) plus the precise reason.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

STORE_ID = "45819330-fb1b-4f3b-b1f6-406dd52f793c"


def _pg_dsn() -> str:
    return re.sub(r"^postgresql\+[^:]+://", "postgresql://", os.environ["DATABASE_URL"])


def banner(s: str) -> None:
    print("\n" + "=" * 90)
    print(s)
    print("=" * 90)


def main() -> None:
    with psycopg2.connect(_pg_dsn()) as conn, conn.cursor() as cur:
        # ---------------------------------------------------------------
        # 1) The medical_stores row itself
        # ---------------------------------------------------------------
        banner("1) MEDICAL STORE ROW")
        cur.execute(
            """
            SELECT id, name, alternate_names, address, unique_code, gst_number,
                   drug_licence, pan, stockist_id,
                   headquarter_id, is_active, created_at, updated_at
            FROM medical_stores
            WHERE id = %s::uuid
            """,
            (STORE_ID,),
        )
        row = cur.fetchone()
        if not row:
            print(f"  No medical_stores row with id={STORE_ID}")
            return
        cols = [d.name for d in cur.description]
        for k, v in zip(cols, row):
            print(f"  {k:18s}: {v}")
        hq_id = row[cols.index("headquarter_id")]

        if hq_id:
            cur.execute(
                "SELECT id, name, code FROM headquarters WHERE id = %s",
                (hq_id,),
            )
            hq = cur.fetchone()
            if hq:
                print(f"  → headquarter      : {hq[1]} (code={hq[2]}, id={hq[0]})")

        # ---------------------------------------------------------------
        # 2) Every doctor linked to this store
        # ---------------------------------------------------------------
        banner("2) doctor_medical_stores (every doctor linked to this store)")
        cur.execute(
            """
            SELECT d.id, d.full_name, d.specialization, d.headquarter_id, d.is_active
            FROM doctor_medical_stores dms
            JOIN doctors d ON d.id = dms.doctor_id
            WHERE dms.medical_store_id = %s::uuid
            ORDER BY d.full_name
            """,
            (STORE_ID,),
        )
        doctors = cur.fetchall()
        print(f"  linked doctors: {len(doctors)}")
        for did, dname, dspec, dhq, dact in doctors:
            print(f"    doctor_id={did}")
            print(f"      name           = {dname}")
            print(f"      specialization = {dspec}")
            print(f"      headquarter_id = {dhq}")
            print(f"      is_active      = {dact}")

        # ---------------------------------------------------------------
        # 3) For EACH linked doctor, list EVERY mr_doctor_allocations row
        #    (active and inactive), with the MR's name + headquarter.
        # ---------------------------------------------------------------
        banner("3) mr_doctor_allocations for each linked doctor")
        for did, dname, *_ in doctors:
            print(f"\n  doctor: {dname}  ({did})")
            cur.execute(
                """
                SELECT mda.id, mda.mr_id, u.full_name, u.email, u.role,
                       mda.is_active, mda.allocated_at, mda.allocated_by
                FROM mr_doctor_allocations mda
                LEFT JOIN users u ON u.id = mda.mr_id
                WHERE mda.doctor_id = %s
                ORDER BY mda.is_active DESC, mda.allocated_at
                """,
                (did,),
            )
            allocs = cur.fetchall()
            if not allocs:
                print("    (no mr_doctor_allocations rows AT ALL for this doctor)")
                continue
            for aid, mid, mname, memail, mrole, act, at, by in allocs:
                tag = "ACTIVE" if act else "inactive"
                print(
                    f"    [{tag:8s}] mr_id={mid}  {mname}  "
                    f"role={mrole}  email={memail}  allocated_at={at}"
                )

        # ---------------------------------------------------------------
        # 4) The exact join the importer runs (active-only), grouped.
        # ---------------------------------------------------------------
        banner("4) IMPORTER'S JOIN (active-only) — what _resolve_mrs_from_stores sees")
        cur.execute(
            """
            SELECT dms.doctor_id, d.full_name, mda.mr_id, u.full_name
            FROM doctor_medical_stores dms
            JOIN mr_doctor_allocations mda
              ON mda.doctor_id = dms.doctor_id
             AND mda.is_active = true
            JOIN doctors d ON d.id = dms.doctor_id
            LEFT JOIN users u ON u.id = mda.mr_id
            WHERE dms.medical_store_id = %s::uuid
            """,
            (STORE_ID,),
        )
        join_rows = cur.fetchall()
        distinct_mrs: set[str] = set()
        print(f"  join_rows = {len(join_rows)}")
        for did, dname, mid, mname in join_rows:
            distinct_mrs.add(str(mid))
            print(f"    via doctor {dname}  →  MR {mname}  ({mid})")
        print(f"\n  → distinct active MRs returned by join = {len(distinct_mrs)}")

        # ---------------------------------------------------------------
        # 5) Verdict
        # ---------------------------------------------------------------
        banner("5) VERDICT")
        if len(distinct_mrs) == 1:
            mid = next(iter(distinct_mrs))
            print(f"  RESOLVED-UNIQUE → mr_id = {mid}")
            print(f"  → import would auto-fill mr_id for this store. Should NOT be in warning.")
        elif len(distinct_mrs) > 1:
            print("  AMBIGUOUS → import leaves mr_id=NULL and emits the warning you saw.")
            print(f"  Reason: store is reachable from {len(distinct_mrs)} distinct active MRs.")
            print("  This happens because at least one linked doctor has >1 active")
            print("  mr_doctor_allocations row (see section 3 above).")
        else:
            print("  UNRESOLVED → no active MR found.")
            print("  Either no doctor is linked, or none of the linked doctors has any")
            print("  is_active=true mr_doctor_allocations row.")

        # ---------------------------------------------------------------
        # 6) Per-doctor diagnostic to pinpoint WHICH doctor is the offender
        # ---------------------------------------------------------------
        banner("6) PER-DOCTOR active-MR COUNT (the offenders are those with > 1)")
        cur.execute(
            """
            SELECT d.id, d.full_name,
                   COUNT(*) FILTER (WHERE mda.is_active = true) AS active_count,
                   COUNT(*) FILTER (WHERE mda.is_active = false) AS inactive_count
            FROM doctor_medical_stores dms
            JOIN doctors d ON d.id = dms.doctor_id
            LEFT JOIN mr_doctor_allocations mda ON mda.doctor_id = d.id
            WHERE dms.medical_store_id = %s::uuid
            GROUP BY d.id, d.full_name
            ORDER BY active_count DESC, d.full_name
            """,
            (STORE_ID,),
        )
        for did, dname, ac, ic in cur.fetchall():
            flag = "  ← offender" if (ac or 0) > 1 else ""
            print(f"  active={ac} inactive={ic}  doctor={dname}  ({did}){flag}")


if __name__ == "__main__":
    main()
