"""
ImportService: orchestrates the full AI import pipeline.

  upload → (background) extract + pre-filter + LLM parse + validate → ready
  preview → return structured_rows to frontend for editing/confirming
  commit → insert confirmed rows into secondary_sales
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation import MrLocationAllocation, MrProductAllocation
from app.models.import_job import ImportJob, ImportJobStatus, ImportSourceType
from app.models.master import Location, Product
from app.models.sale import SecondarySale
from app.models.user import User
from app.modules.sales.importer.extractor import extract
from app.modules.sales.importer.llm_parser import (
    EntityCandidate,
    LLMParseRequest,
    parse_with_llm,
)
from app.modules.sales.importer.validator import validate_rows

logger = logging.getLogger(__name__)


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
            job.status = ImportJobStatus.failed
            job.error_message = str(exc)
            await db.flush()

    async def _do_process(
        self,
        db: AsyncSession,
        job: ImportJob,
        content: bytes,
    ) -> None:
        # --- Extract raw text ---
        result = extract(job.filename, content)
        job.raw_text = result.text
        if result.detected_fos_name and job.mr_id is None:
            job.detected_fos_name = result.detected_fos_name

        # --- Build entity candidate lists (pre-filter medical stores) ---
        products = await self._get_all_products(db)
        medical_stores = await self._prefilter_stores(db, result.text)
        mrs = await self._get_all_mrs(db)
        doctors = await self._get_all_doctors(db)

        # --- LLM call ---
        req = LLMParseRequest(
            raw_text=result.text,
            products=products,
            medical_stores=medical_stores,
            mrs=mrs,
            doctors=doctors,
            detected_fos_name=result.detected_fos_name,
        )
        llm_resp = parse_with_llm(req)
        job.llm_response = llm_resp.raw_response
        job.model_used = llm_resp.model_used
        job.total_rows = len(llm_resp.rows)

        # --- Deterministic validation ---
        validated = await validate_rows(db, llm_resp.rows, job_mr_id=job.mr_id)
        job.structured_rows = validated
        job.status = ImportJobStatus.ready
        await db.flush()

    # ------------------------------------------------------------------
    # Step 3: commit confirmed rows
    # ------------------------------------------------------------------

    async def commit_job(
        self,
        db: AsyncSession,
        job: ImportJob,
        confirmed_rows: list[dict],
        committed_by: User,
    ) -> int:
        """
        Insert confirmed_rows into secondary_sales.
        confirmed_rows comes from the frontend after user edits;
        we re-validate each row before inserting.
        Returns the count of committed rows.
        """
        validated = await validate_rows(db, confirmed_rows, job_mr_id=job.mr_id)
        committed = 0

        for row in validated:
            if not row.get("is_valid") or row.get("skip"):
                continue

            mr_id = uuid.UUID(row["mr_id"]) if row.get("mr_id") else None
            product_id = uuid.UUID(row["product_id"]) if row.get("product_id") else None

            if mr_id is None or product_id is None:
                continue

            # Resolve location context from MR's first active location allocation
            location_id, state_id, hq_id, division_id = await self._resolve_mr_location(
                db, mr_id, product_id
            )
            if location_id is None:
                row.setdefault("errors", []).append(
                    "Could not resolve location for this MR and product"
                )
                continue

            # Snapshot PTR/PTS/MRP from product master as fallback
            product = await db.get(Product, product_id)
            if product is None:
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
        job.status = ImportJobStatus.committed
        await db.flush()
        return committed

    # ------------------------------------------------------------------
    # Entity helpers
    # ------------------------------------------------------------------

    async def _get_all_products(self, db: AsyncSession) -> list[EntityCandidate]:
        rows = (await db.execute(select(Product.id, Product.name).where(Product.is_active.is_(True)))).all()
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    async def _get_all_mrs(self, db: AsyncSession) -> list[EntityCandidate]:
        from app.models.enums import UserRole
        rows = (
            await db.execute(
                select(User.id, User.full_name).where(User.role == UserRole.MR, User.is_active.is_(True))
            )
        ).all()
        return [EntityCandidate(id=str(r[0]), name=r[1]) for r in rows]

    async def _get_all_doctors(self, db: AsyncSession) -> list[EntityCandidate]:
        from app.models.doctor import Doctor
        rows = (
            await db.execute(select(Doctor.id, Doctor.name).where(Doctor.is_active.is_(True)))
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
                Location.division_id == product.division_id,
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
