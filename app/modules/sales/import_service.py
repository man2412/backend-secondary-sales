"""
ImportService: orchestrates the full AI import pipeline.

  upload → (background) extract → chunk → parallel LLM parse → merge → validate → ready/partial
  preview → return structured_rows + extraction_warnings to frontend
  commit → insert confirmed rows into secondary_sales
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrLocationAllocation, MrProductAllocation
from app.models.import_job import ImportJob, ImportJobStatus, ImportSourceType
from app.models.master import Location, Product
from app.models.sale import SecondarySale
from app.models.user import User
from app.modules.sales.importer.extractor import Chunk, ExtractionResult, extract
from app.modules.sales.importer.llm_parser import (
    EntityCandidate,
    LLMParseRequest,
    parse_with_llm,
)
from app.modules.sales.importer.validator import validate_rows

logger = logging.getLogger(__name__)


CHUNK_CONCURRENCY = 5
ROW_COUNT_WARN_THRESHOLD = 0.8  # warn if LLM returns < 80% of heuristic row count


@dataclass
class _ChunkOutcome:
    rows: list[dict]
    model_used: str | None
    error: str | None


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
        mr_id: uuid.UUID | None,
    ) -> ImportJob:
        ext = filename.rsplit(".", 1)[-1].lower()
        try:
            source_type = ImportSourceType(ext)
        except ValueError:
            raise ValueError(f"Unsupported file format: {ext!r}. Supported: pdf, csv, xlsx, xls")

        file_hash = hashlib.sha256(content).hexdigest()

        job = ImportJob(
            filename=filename,
            file_hash=file_hash,
            source_type=source_type,
            uploaded_by=uploaded_by,
            mr_id=mr_id,
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
            # Use a brand-new session so a poisoned/stale connection from the long
            # background call cannot prevent the failure status from persisting.
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
                # Best-effort fallback on the original session
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
        # --- Extract raw text and split into chunks ---
        result: ExtractionResult = extract(job.filename, content)
        job.raw_text = result.text
        if result.detected_fos_name and job.mr_id is None:
            job.detected_fos_name = result.detected_fos_name

        # --- Build entity candidate lists (shared across chunks) ---
        products = await self._get_all_products(db, raw_text=result.text)
        medical_stores = await self._prefilter_stores(db, result.text)
        mrs = await self._get_all_mrs(db, job_mr_id=job.mr_id)
        doctors = await self._get_all_doctors(db)

        # --- Parallel LLM calls, one per chunk ---
        chunk_outcomes = await self._process_chunks_parallel(
            chunks=result.chunks,
            products=products,
            medical_stores=medical_stores,
            mrs=mrs,
            doctors=doctors,
            detected_fos_name=result.detected_fos_name,
        )

        # --- Merge chunk results + collect warnings ---
        merged_rows: list[dict] = []
        warnings: list[str] = []
        succeeded = 0
        models_used: set[str] = set()
        for chunk, outcome in zip(result.chunks, chunk_outcomes):
            if outcome.error:
                warnings.append(
                    f"chunk {chunk.index + 1}/{len(result.chunks)}: failed after retries — {outcome.error}"
                )
                continue
            succeeded += 1
            if outcome.model_used:
                models_used.add(outcome.model_used)
            merged_rows.extend(outcome.rows)
            # Row-count sanity check (only meaningful if heuristic found rows)
            if chunk.expected_rows >= 5:
                ratio = len(outcome.rows) / chunk.expected_rows
                if ratio < ROW_COUNT_WARN_THRESHOLD:
                    warnings.append(
                        f"chunk {chunk.index + 1}/{len(result.chunks)}: "
                        f"expected ~{chunk.expected_rows} rows, got {len(outcome.rows)} "
                        f"({int(ratio * 100)}%) — possible silent drop"
                    )

        # --- Deterministic validation ---
        validated = await validate_rows(db, merged_rows, job_mr_id=job.mr_id)

        # --- Up-front allocation warning (so user learns at preview, not after commit) ---
        mr_ids_in_rows: set[uuid.UUID] = set()
        if job.mr_id is not None:
            mr_ids_in_rows.add(job.mr_id)
        for r in validated:
            if r.get("mr_id"):
                try:
                    mr_ids_in_rows.add(uuid.UUID(r["mr_id"]))
                except ValueError:
                    pass
        mrs_without_allocations = await self._find_mrs_without_allocations(db, mr_ids_in_rows)
        if mrs_without_allocations:
            names = await self._get_user_names(db, mrs_without_allocations)
            warnings.append(
                "The following MR(s) have no active location allocations — commits for their rows will be skipped: "
                + ", ".join(names)
            )

        # --- Persist job state ---
        job.structured_rows = validated
        job.total_rows = len(validated)
        job.model_used = ", ".join(sorted(models_used)) if models_used else None
        job.chunks_total = len(result.chunks)
        job.chunks_succeeded = succeeded
        job.extraction_warnings = warnings if warnings else None

        if succeeded == 0:
            raise RuntimeError(
                "All chunks failed to process. First error: "
                + (warnings[0] if warnings else "unknown")
            )
        if succeeded < len(result.chunks) or warnings:
            job.status = ImportJobStatus.partial
        else:
            job.status = ImportJobStatus.ready
        await db.flush()

    # ------------------------------------------------------------------
    # Parallel chunk processing
    # ------------------------------------------------------------------

    async def _process_chunks_parallel(
        self,
        *,
        chunks: list[Chunk],
        products: list[EntityCandidate],
        medical_stores: list[EntityCandidate],
        mrs: list[EntityCandidate],
        doctors: list[EntityCandidate],
        detected_fos_name: str | None,
    ) -> list["_ChunkOutcome"]:
        sem = asyncio.Semaphore(CHUNK_CONCURRENCY)

        async def run(chunk: Chunk) -> _ChunkOutcome:
            async with sem:
                try:
                    req = LLMParseRequest(
                        raw_text=chunk.text,
                        products=products,
                        medical_stores=medical_stores,
                        mrs=mrs,
                        doctors=doctors,
                        detected_fos_name=detected_fos_name,
                    )
                    # parse_with_llm is sync (blocking HTTP); run in thread to release the loop.
                    resp = await asyncio.to_thread(parse_with_llm, req)
                    return _ChunkOutcome(rows=resp.rows, model_used=resp.model_used, error=None)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Chunk %d failed permanently: %s", chunk.index, exc)
                    return _ChunkOutcome(rows=[], model_used=None, error=f"{type(exc).__name__}: {exc}")

        return await asyncio.gather(*(run(c) for c in chunks))

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
        Insert confirmed_rows into secondary_sales.
        Returns a structured summary with counts and skipped-row details
        so the frontend can show the user exactly why any rows were skipped.
        """
        validated = await validate_rows(db, confirmed_rows, job_mr_id=job.mr_id)
        committed = 0
        skipped_rows: list[dict] = []

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

            location_id, state_id, hq_id, division_id = await self._resolve_mr_location(
                db, mr_id, product_id
            )
            if location_id is None:
                reason = await self._explain_allocation_failure(db, mr_id, product_id)
                _skip(reason)
                continue

            product = await db.get(Product, product_id)
            if product is None:
                _skip(f"product {product_id} not found")
                continue

            ptr = row.get("ptr") or float(product.ptr)
            pts = float(product.pts) if product.pts else None
            mrp = row.get("mrp") or float(product.mrp)

            sale_date = row["sale_date"]
            if isinstance(sale_date, str):
                sale_date = date.fromisoformat(sale_date)

            sale = SecondarySale(
                mr_id=mr_id,
                product_id=product_id,
                doctor_id=uuid.UUID(row["doctor_id"]) if row.get("doctor_id") else None,
                medical_store_id=uuid.UUID(row["medical_store_id"]) if row.get("medical_store_id") else None,
                division_id=division_id,
                headquarter_id=hq_id,
                location_id=location_id,
                state_id=state_id,
                sale_date=sale_date,
                sale_qty=int(row["sale_qty"]),
                free_qty=int(row.get("free_qty") or 0),
                ptr=ptr,
                pts=pts,
                mrp=mrp,
                reported_amount=row.get("reported_amount"),
                bill_ref=row.get("bill_ref"),
                batch=row.get("batch"),
                pack=row.get("pack"),
                special_price=None,
                remarks=row.get("remarks"),
                is_active=True,
            )
            db.add(sale)
            committed += 1

        await db.flush()
        job.committed_count = committed
        # Only flip to 'committed' if at least one row was actually inserted.
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
    # Entity helpers
    # ------------------------------------------------------------------

    async def _get_all_products(
        self,
        db: AsyncSession,
        raw_text: str | None = None,
    ) -> list[EntityCandidate]:
        """Pre-filter products to those whose name appears in the raw text (case-insensitive).
        Falls back to first 200 products if no matches.
        """
        import re

        rows_all = (
            await db.execute(select(Product.id, Product.name).where(Product.is_active.is_(True)))
        ).all()
        if not raw_text:
            return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows_all[:200]]

        text_upper = raw_text.upper()
        matched: list[EntityCandidate] = []
        for r in rows_all:
            name = (r[1] or "").strip()
            if not name:
                continue
            # Use first significant token of product name (length >=4) for cheap matching
            tokens = [t for t in re.split(r"[\s,/\-]+", name.upper()) if len(t) >= 4]
            if not tokens:
                continue
            if any(t in text_upper for t in tokens):
                matched.append(EntityCandidate(id=str(r[0]), name=name))
        if matched:
            return matched[:300]
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows_all[:200]]

    async def _get_all_mrs(
        self,
        db: AsyncSession,
        job_mr_id: uuid.UUID | None = None,
    ) -> list[EntityCandidate]:
        """If a specific MR is set on the job, only send that one (single-MR file).
        Otherwise, send up to 200 active MRs."""
        from app.models.enums import UserRole
        stmt = select(User.id, User.full_name).where(
            User.role == UserRole.MR, User.is_active.is_(True)
        )
        if job_mr_id is not None:
            stmt = stmt.where(User.id == job_mr_id)
        else:
            stmt = stmt.limit(200)
        rows = (await db.execute(stmt)).all()
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    async def _get_all_doctors(self, db: AsyncSession) -> list[EntityCandidate]:
        from app.models.doctor import Doctor
        rows = (
            await db.execute(
                select(Doctor.id, Doctor.full_name)
                .where(Doctor.is_active.is_(True))
                .limit(200)
            )
        ).all()
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    async def _prefilter_stores(self, db: AsyncSession, raw_text: str) -> list[EntityCandidate]:
        """
        Extract candidate store names from raw text and query only matching stores,
        reducing 6,243+ store tokens to ~80 relevant candidates.
        """
        from app.models.stockist import MedicalStore
        import re

        # Extract ALLCAPS words/phrases as candidate names
        caps_tokens = re.findall(r"[A-Z][A-Z &,.\-]{3,}", raw_text)
        unique_tokens = list({t.strip() for t in caps_tokens if len(t.strip()) > 4})[:50]

        if not unique_tokens:
            # Fall back to returning all (capped at 200 for prompt safety)
            rows = (
                await db.execute(
                    select(MedicalStore.id, MedicalStore.name)
                    .where(MedicalStore.is_active.is_(True))
                    .limit(200)
                )
            ).all()
            return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

        # Build ILIKE ANY query
        patterns = [f"%{t}%" for t in unique_tokens]
        stmt = (
            select(MedicalStore.id, MedicalStore.name)
            .where(MedicalStore.is_active.is_(True))
            .where(
                MedicalStore.name.op("ILIKE")(
                    text("ANY(:patterns)").bindparams(patterns=patterns)
                )
            )
            .limit(150)
        )
        rows = (await db.execute(stmt)).all()

        if not rows:
            # No pattern matches → return a sample so LLM still has some context
            rows = (
                await db.execute(
                    select(MedicalStore.id, MedicalStore.name)
                    .where(MedicalStore.is_active.is_(True))
                    .limit(100)
                )
            ).all()

        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    async def _explain_allocation_failure(
        self,
        db: AsyncSession,
        mr_id: uuid.UUID,
        product_id: uuid.UUID,
    ) -> str:
        """Produce a human-readable reason when _resolve_mr_location returns None."""
        from app.models.master import Division, Headquarter

        mr_row = (await db.execute(select(User.full_name).where(User.id == mr_id))).one_or_none()
        mr_name = mr_row[0] if mr_row else str(mr_id)

        product = await db.get(Product, product_id)
        product_name = product.name if product else str(product_id)
        product_div_id = product.division_id if product else None
        product_div_name = None
        if product_div_id is not None:
            pdr = (
                await db.execute(select(Division.name).where(Division.id == product_div_id))
            ).one_or_none()
            product_div_name = pdr[0] if pdr else str(product_div_id)

        alloc_count = (
            await db.execute(
                select(MrLocationAllocation.id)
                .where(MrLocationAllocation.mr_id == mr_id)
                .where(MrLocationAllocation.is_active.is_(True))
            )
        ).all()

        if not alloc_count:
            return (
                f"MR '{mr_name}' has no active location allocations. "
                f"Create one in mr_location_allocations before committing."
            )

        return (
            f"MR '{mr_name}' has {len(alloc_count)} active location allocation(s), "
            f"but none of them belong to a headquarter that covers division "
            f"'{product_div_name or product_div_id}' (product: {product_name}). "
            f"Either assign the MR to an HQ that includes this division, "
            f"or add this division to one of the MR's current HQs."
        )

    async def _explain_allocation_failure(
        self,
        db: AsyncSession,
        mr_id: uuid.UUID,
        product_id: uuid.UUID,
    ) -> str:
        """Produce a human-readable reason when _resolve_mr_location returns None."""
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
            f"but none of them belong to a headquarter that covers division "
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

    async def _resolve_mr_location(
        self,
        db: AsyncSession,
        mr_id: uuid.UUID,
        product_id: uuid.UUID,
    ) -> tuple[uuid.UUID | None, uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]:
        """
        Returns (location_id, state_id, headquarter_id, division_id) for the MR.
        Uses the MR's first active location allocation that matches the product's division.
        """
        from app.models.master import Headquarter, State

        product = await db.get(Product, product_id)
        if product is None:
            return None, None, None, None

        stmt = (
            select(
                MrLocationAllocation.location_id,
                Location.headquarter_id,
            )
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

        state_id = hq.state_id
        division_id = product.division_id
        return location_id, state_id, hq_id, division_id
