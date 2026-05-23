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
import uuid
from dataclasses import dataclass
from datetime import date

import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrDoctorAllocation, MrLocationAllocation
from app.models.doctor import Doctor, DoctorMedicalStore
from app.models.import_job import ImportJob, ImportJobStatus, ImportSourceType
from app.models.master import Headquarter, Location, Product
from app.models.sale import SecondarySale
from app.models.stockist import MedicalStore
from app.models.user import User
from app.modules.sales.importer.extractor import ExtractionResult, extract
from app.modules.sales.importer.llm_parser import LLMParseRequest, LLMParseResponse, parse_with_llm
from app.modules.sales.importer.validator import validate_rows

logger = logging.getLogger(__name__)


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
        try:
            await self._do_process(db, job, content)
        except Exception as exc:
            logger.exception("ImportJob %s failed", job.id)
            from app.core.database import AsyncSessionLocal
            err_msg = str(exc)
            try:
                async with AsyncSessionLocal() as fresh:
                    async with fresh.begin():
                        fresh_job = await fresh.get(ImportJob, job.id)
                        if fresh_job is not None:
                            fresh_job.status = ImportJobStatus.failed
                            fresh_job.error_message = err_msg
            except Exception:
                logger.exception("ImportJob %s: failed to persist failure status", job.id)
                try:
                    job.status = ImportJobStatus.failed
                    job.error_message = err_msg
                    await db.flush()
                except Exception:
                    pass

    async def _do_process(
        self,
        db: AsyncSession,
        job: ImportJob,
        content: bytes,
    ) -> None:
        # 1. Extract: PDFs → raw bytes; tabular → minimal CSV text.
        #    FOS-hint detection is no longer needed for resolution (mr_id is
        #    now derived from medical_store → doctor → MR allocation), so we
        #    skip the second PDF parse entirely. Tabular extraction picks up
        #    the FOS name for free as it scans rows; we keep that for display.
        result: ExtractionResult = extract(job.filename, content, detect_fos=False)
        job.raw_text = result.raw_text or ""
        if result.detected_fos_name:
            job.detected_fos_name = result.detected_fos_name

        # 2. Single LLM call — no entity lists, just file/text
        req = LLMParseRequest(
            raw_text=result.raw_text,
            pdf_bytes=result.raw_bytes,
            is_pdf=result.is_pdf,
            detected_fos_name=None,
        )
        resp: LLMParseResponse = await asyncio.to_thread(parse_with_llm, req)

        # 3. Load entity catalogues for backend fuzzy matching
        #    (MRs are resolved from store → doctor allocations, not by name)
        products = await self._get_all_products(db)
        medical_stores = await self._get_all_stores(db)
        doctors = await self._get_all_doctors(db)

        # 4. Fuzzy resolve product / store / doctor names → UUIDs
        resolved_rows = self._fuzzy_resolve_entities(
            resp.rows, products, medical_stores, doctors
        )

        # 5. Resolve mr_id from each row's medical_store_id via:
        #       doctor_medical_stores → mr_doctor_allocations (is_active)
        #    Returns warnings for stores with 0 or multiple distinct MRs.
        mr_resolution_warnings = await self._resolve_mrs_from_stores(db, resolved_rows)

        # 6. Deterministic validation + type coercion
        validated = await validate_rows(db, resolved_rows)

        # 6b. Display names follow DB for resolved ids; null ids → null names (user fills in UI)
        await self._hydrate_raw_names_from_db(db, validated)

        # 6c. Auto-populate derived fields (division/location/hq/state + doctor auto-fill)
        await self._enrich_derived_fields(db, validated)

        # 7. Up-front allocation warning so user sees it at preview, not after commit
        mr_ids_in_rows: set[uuid.UUID] = set()
        for r in validated:
            if r.get("mr_id"):
                try:
                    mr_ids_in_rows.add(uuid.UUID(r["mr_id"]))
                except ValueError:
                    pass
        mrs_without_alloc = await self._find_mrs_without_allocations(db, mr_ids_in_rows)
        warnings: list[str] = list(mr_resolution_warnings)
        if mrs_without_alloc:
            names = await self._get_user_names(db, mrs_without_alloc)
            warnings.append(
                "The following MR(s) have no active location allocations — "
                "commits for their rows will be skipped: " + ", ".join(names)
            )

        # 8. Persist
        job.structured_rows = validated
        job.total_rows = len(validated)
        job.model_used = resp.model_used or None
        job.chunks_total = 1
        job.chunks_succeeded = 1
        job.extraction_warnings = warnings if warnings else None
        job.status = ImportJobStatus.partial if warnings else ImportJobStatus.ready
        await db.flush()

    # ------------------------------------------------------------------
    # Fuzzy entity resolution
    # ------------------------------------------------------------------

    def _fuzzy_resolve_entities(
        self,
        rows: list[dict],
        products: list[EntityCandidate],
        medical_stores: list[EntityCandidate],
        doctors: list[EntityCandidate],
    ) -> list[dict]:
        """
        Five-level fuzzy match raw LLM-extracted names to entity UUIDs:

          1. Exact match on raw (case-insensitive)
          2. Normalized exact   (upper-cased, punctuation stripped, whitespace collapsed)
          3. Substring containment (either direction)
          4. First significant token (first word >=4 chars present in candidate tokens)
          5. difflib.get_close_matches with cutoff 0.6

        Unresolved names are left as None — the frontend handles manual assignment.

        Note: MR names are NOT fuzzy-matched here. mr_id is resolved separately
        via `_resolve_mrs_from_stores` using the medical-store → doctor → MR
        allocation chain.

        Implementation note: levels 1-2 are O(1) dict lookups; levels 3-4 are
        linear scans (kept on the precomputed normalized list); level 5 uses
        difflib. Per-row results are also memoized so repeated raw names in
        large files cost nothing after the first row.
        """
        prod_uppers = [(c.name or "").upper() for c in products]
        store_uppers = [(c.name or "").upper() for c in medical_stores]
        doc_uppers = [(c.name or "").upper() for c in doctors]

        prod_norms = [_normalize(c.name) for c in products]
        store_norms = [_normalize(c.name) for c in medical_stores]
        doc_norms = [_normalize(c.name) for c in doctors]

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

        # Per-entity-type cache so repeated raw names don't redo the heavy work.
        _SENTINEL = object()
        prod_cache: dict[str, str | None] = {}
        store_cache: dict[str, str | None] = {}
        doc_cache: dict[str, str | None] = {}

        def match(
            raw: str | None,
            candidates: list[EntityCandidate],
            upper_map: dict[str, str],
            norm_map: dict[str, str],
            norms: list[str],
            cache: dict[str, str | None],
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
        for row in rows:
            r = dict(row)

            if not r.get("product_id"):
                r["product_id"] = match(
                    r.get("product_name_raw"), products,
                    prod_upper_map, prod_norm_map, prod_norms, prod_cache,
                )

            if not r.get("medical_store_id"):
                r["medical_store_id"] = match(
                    r.get("customer_name_raw"), medical_stores,
                    store_upper_map, store_norm_map, store_norms, store_cache,
                )

            # mr_id is intentionally NOT set here — it is resolved per-row
            # afterwards via `_resolve_mrs_from_stores`.

            if not r.get("doctor_id"):
                r["doctor_id"] = match(
                    r.get("doctor_name_raw"), doctors,
                    doc_upper_map, doc_norm_map, doc_norms, doc_cache,
                )

            resolved.append(r)

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

        validated = await validate_rows(db, confirmed_rows)
        # `_hydrate_raw_names_from_db` is intentionally skipped here:
        # the commit response only returns counts + skipped_rows, and the raw
        # name fields are not written to `secondary_sales`. Re-fetching them
        # was pure dead work.
        await self._enrich_derived_fields(db, validated)

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

        product_cache: dict[uuid.UUID, Product] = {}
        if product_ids_needed:
            rs = (
                await db.execute(
                    select(Product).where(Product.id.in_(product_ids_needed))
                )
            ).scalars().all()
            product_cache = {p.id: p for p in rs}

        # Block whole commit if any non-skipped row still lacks product, store, or MR id
        bad_indexes = [
            idx
            for idx, row in enumerate(validated)
            if not row.get("skip")
            and (not row.get("product_id") or not row.get("medical_store_id") or not row.get("mr_id"))
        ]
        if bad_indexes:
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

        for idx, row in enumerate(validated):
            row_errors: list[str] = list(row.get("errors") or [])

            def _skip(reason: str) -> None:
                row_errors.append(reason)
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

            if not all([location_id, hq_id, state_id, division_id]):
                _skip(
                    "could not derive location chain — ensure the medical store (or doctor) "
                    "has a location_id, the location maps to a headquarter, and the product has a division"
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
                "sale_qty": int(row["sale_qty"]),
                "free_qty": int(row.get("free_qty") or 0),
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

        committed = len(sale_values)
        if sale_values:
            from sqlalchemy import insert as sa_insert
            await db.execute(sa_insert(SecondarySale), sale_values)

        job.committed_count = committed
        if committed > 0:
            job.status = ImportJobStatus.committed
        await db.flush()

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
        rows = (
            await db.execute(select(Product.id, Product.name).where(Product.is_active.is_(True)))
        ).all()
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    async def _get_all_stores(self, db: AsyncSession) -> list[EntityCandidate]:
        from app.models.stockist import MedicalStore
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
        return candidates

    async def _get_all_doctors(self, db: AsyncSession) -> list[EntityCandidate]:
        from app.models.doctor import Doctor
        rows = (
            await db.execute(
                select(Doctor.id, Doctor.full_name).where(Doctor.is_active.is_(True))
            )
        ).all()
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    # ------------------------------------------------------------------
    # MR resolution (medical store → doctor → active MR allocation)
    # ------------------------------------------------------------------

    async def _resolve_mrs_from_stores(
        self,
        db: AsyncSession,
        rows: list[dict],
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

        store_ids: set[uuid.UUID] = set()
        for row in rows:
            if s := _pid(row.get("medical_store_id")):
                store_ids.add(s)

        if not store_ids:
            return []

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

        # Apply resolved mr_ids to rows. We override any pre-existing mr_id
        # because this lookup is now the authoritative source.
        for row in rows:
            sid = _pid(row.get("medical_store_id"))
            if sid is None:
                row["mr_id"] = None
                continue
            mid = store_to_resolved_mr.get(sid)
            row["mr_id"] = str(mid) if mid is not None else None

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

    async def _hydrate_raw_names_from_db(self, db: AsyncSession, rows: list[dict]) -> None:
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

    async def _enrich_derived_fields(self, db: AsyncSession, rows: list[dict]) -> None:
        """
        Auto-populate derived fields on each row using batch DB lookups:

          division_id    <- product.division_id
          location_id    <- medical_store.location_id (fallback: doctor.location_id)
          headquarter_id <- location.headquarter_id
          state_id       <- headquarter.state_id

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

        # ---------- Step B: batch fetch products / stores / doctors ----------
        product_map: dict[uuid.UUID, uuid.UUID] = {}  # product_id -> division_id
        if product_ids:
            rs = (
                await db.execute(
                    select(Product.id, Product.division_id).where(Product.id.in_(product_ids))
                )
            ).all()
            product_map = {r[0]: r[1] for r in rs}

        store_map: dict[uuid.UUID, uuid.UUID | None] = {}  # store_id -> location_id
        if store_ids:
            rs = (
                await db.execute(
                    select(MedicalStore.id, MedicalStore.location_id).where(
                        MedicalStore.id.in_(store_ids)
                    )
                )
            ).all()
            store_map = {r[0]: r[1] for r in rs}

        doctor_map: dict[uuid.UUID, uuid.UUID | None] = {}  # doctor_id -> location_id
        if doctor_ids:
            rs = (
                await db.execute(
                    select(Doctor.id, Doctor.location_id).where(Doctor.id.in_(doctor_ids))
                )
            ).all()
            doctor_map = {r[0]: r[1] for r in rs}

        # ---------- Step C: gather location ids from stores + doctors ----------
        location_ids: set[uuid.UUID] = set()
        for v in store_map.values():
            if v is not None:
                location_ids.add(v)
        for v in doctor_map.values():
            if v is not None:
                location_ids.add(v)

        location_map: dict[uuid.UUID, uuid.UUID] = {}  # location_id -> hq_id
        if location_ids:
            rs = (
                await db.execute(
                    select(Location.id, Location.headquarter_id).where(
                        Location.id.in_(location_ids)
                    )
                )
            ).all()
            location_map = {r[0]: r[1] for r in rs}

        # ---------- Step D: fetch headquarter -> state_id ----------
        hq_ids: set[uuid.UUID] = {v for v in location_map.values() if v is not None}
        hq_map: dict[uuid.UUID, uuid.UUID] = {}  # hq_id -> state_id
        if hq_ids:
            rs = (
                await db.execute(
                    select(Headquarter.id, Headquarter.state_id).where(Headquarter.id.in_(hq_ids))
                )
            ).all()
            hq_map = {r[0]: r[1] for r in rs}

        # ---------- Step E: per-row derivation ----------
        for row in rows:
            pid = _pid(row.get("product_id"))
            sid = _pid(row.get("medical_store_id"))
            did = _pid(row.get("doctor_id"))

            division_id = product_map.get(pid) if pid else None

            location_id: uuid.UUID | None = None
            if sid is not None:
                location_id = store_map.get(sid)
            if location_id is None and did is not None:
                location_id = doctor_map.get(did)

            hq_id = location_map.get(location_id) if location_id else None
            state_id = hq_map.get(hq_id) if hq_id else None

            row["division_id"] = str(division_id) if division_id else None
            row["location_id"] = str(location_id) if location_id else None
            row["headquarter_id"] = str(hq_id) if hq_id else None
            row["state_id"] = str(state_id) if state_id else None

    # ------------------------------------------------------------------
    # Allocation helpers
    # ------------------------------------------------------------------

    async def _resolve_mr_location(
        self,
        db: AsyncSession,
        mr_id: uuid.UUID,
        product_id: uuid.UUID,
    ) -> tuple[uuid.UUID | None, uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]:
        from app.models.master import Headquarter

        product = await db.get(Product, product_id)
        if product is None:
            return None, None, None, None

        stmt = (
            select(MrLocationAllocation.location_id, Location.headquarter_id)
            .join(Location, MrLocationAllocation.location_id == Location.id)
            .join(Headquarter, Location.headquarter_id == Headquarter.id)
            .where(
                MrLocationAllocation.mr_id == mr_id,
                MrLocationAllocation.is_active.is_(True),
                Headquarter.division_ids.contains([product.division_id]),
            )
            .limit(1)
        )
        row = (await db.execute(stmt)).one_or_none()
        if row is None:
            return None, None, None, None

        location_id, hq_id = row
        hq = await db.get(Headquarter, hq_id)
        if hq is None:
            return None, None, None, None

        return location_id, hq.state_id, hq_id, product.division_id

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
                select(MrLocationAllocation.id)
                .where(MrLocationAllocation.mr_id == mr_id)
                .where(MrLocationAllocation.is_active.is_(True))
            )
        ).all()

        if not alloc_rows:
            return (
                f"MR '{mr_name}' has no active location allocations. "
                f"Create one in mr_location_allocations before committing."
            )

        return (
            f"MR '{mr_name}' has {len(alloc_rows)} active location allocation(s), "
            f"but none belong to a headquarter covering division "
            f"'{product_div_name or 'unknown'}' (product: {product_name}). "
            f"Either assign the MR to an HQ that includes this division, "
            f"or add this division to one of the MR's current HQs."
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
                select(MrLocationAllocation.mr_id)
                .where(MrLocationAllocation.mr_id.in_(mr_ids))
                .where(MrLocationAllocation.is_active.is_(True))
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
