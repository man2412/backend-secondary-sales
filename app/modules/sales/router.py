import logging
import time
from typing import Annotated
from datetime import date
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_roles
from app.core.database import get_db
from app.core.responses import PaginationMeta, ok
from app.models.enums import UserRole
from app.models.import_job import ImportJob, ImportJobStatus
from app.models.user import User
from app.modules.sales.import_service import ImportService
from app.modules.sales.schemas import (
    ImportJobCommitBody,
    ImportJobOut,
    ImportJobPreviewOut,
    SecondarySaleCreate,
    SecondarySaleOut,
    SecondarySaleUpdate,
)
from app.modules.sales.service import SalesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/secondary-sales", tags=["secondary-sales"])


# ---------------------------------------------------------------------------
# Secondary Sales CRUD
# ---------------------------------------------------------------------------

@router.get("")
async def list_secondary_sales(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    sale_date: Annotated[date | None, Query()] = None,
    mr_id: Annotated[UUID | None, Query()] = None,
    doctor_id: Annotated[UUID | None, Query()] = None,
    product_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    try:
        rows, total = await SalesService().list_sales(
            db,
            current,
            page=page,
            per_page=per_page,
            sale_date=sale_date,
            mr_id_filter=mr_id,
            doctor_id_filter=doctor_id,
            product_id_filter=product_id,
            date_from=date_from,
            date_to=date_to,
            include_inactive=include_inactive,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    total_pages = (total + per_page - 1) // per_page if total else 0
    data = [SecondarySaleOut.model_validate(r).model_dump(mode="json") for r in rows]
    return ok(
        data=data,
        pagination=PaginationMeta(page=page, per_page=per_page, total=total, total_pages=total_pages),
    )


@router.get("/{sale_id}")
async def get_secondary_sale(
    sale_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        row = await SalesService().get_sale(db, current, sale_id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sale not found")
    return ok(data=SecondarySaleOut.model_validate(row).model_dump(mode="json"))


@router.post("")
async def create_secondary_sale(
    body: SecondarySaleCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    try:
        row = await SalesService().create_sale(db, current, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=SecondarySaleOut.model_validate(row).model_dump(mode="json"), message="Sale created")


@router.put("/{sale_id}")
async def update_secondary_sale(
    sale_id: UUID,
    body: SecondarySaleUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN))],
) -> dict:
    try:
        row = await SalesService().update_sale(db, current, sale_id, body)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(data=SecondarySaleOut.model_validate(row).model_dump(mode="json"), message="Sale updated")


@router.delete("/{sale_id}")
async def delete_secondary_sale(
    sale_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN))],
) -> dict:
    try:
        await SalesService().delete_sale(db, current, sale_id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ok(message="Sale removed")


# ---------------------------------------------------------------------------
# AI Import Jobs
# ---------------------------------------------------------------------------

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_PDF_PAGES = 150
MAX_TABULAR_ROWS = 10_000


@router.post("/import-jobs", dependencies=[Depends(require_roles(UserRole.SUPER_ADMIN))])
async def upload_import_job(
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(description="Upload .pdf, .xlsx, .xls, or .csv")],
) -> dict:
    """
    Upload a distributor sales file. Processing (LLM parse + validation) runs in the background.
    Poll GET /import-jobs/{id}/preview until status = ready.

    `mr_id` is no longer accepted at upload — every row's MR is resolved from
    its medical store (doctors at that store → active MR allocation).

    Limits: 10 MB / 150 PDF pages / 10,000 Excel-CSV rows.
    """
    content = await file.read()
    filename = file.filename or "upload"

    logger.info(
        "upload_import_job: received filename=%r bytes=%d uploaded_by=%s",
        filename, len(content), str(current.id)[:8],
    )

    if len(content) > MAX_FILE_SIZE_BYTES:
        logger.warning(
            "upload_import_job: REJECTED filename=%r — bytes=%d exceeds limit %d",
            filename, len(content), MAX_FILE_SIZE_BYTES,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB limit",
        )

    from app.modules.sales.importer.extractor import probe_size
    pages, rows = probe_size(filename, content)
    if pages is not None and pages > MAX_PDF_PAGES:
        logger.warning(
            "upload_import_job: REJECTED filename=%r — pages=%d exceeds limit %d",
            filename, pages, MAX_PDF_PAGES,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"PDF has {pages} pages; max allowed is {MAX_PDF_PAGES}",
        )
    if rows is not None and rows > MAX_TABULAR_ROWS:
        logger.warning(
            "upload_import_job: REJECTED filename=%r — rows=%d exceeds limit %d",
            filename, rows, MAX_TABULAR_ROWS,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Spreadsheet has {rows} rows; max allowed is {MAX_TABULAR_ROWS}",
        )

    try:
        job = await ImportService().create_job(
            db,
            filename=filename,
            content=content,
            uploaded_by=current.id,
        )
    except ValueError as e:
        logger.warning(
            "upload_import_job: create_job rejected filename=%r — %s",
            filename, e,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Commit the job row first, then process in background
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(_run_processing, job.id, content)
    logger.info(
        "upload_import_job: queued background processing job_id=%s",
        str(job.id)[:8],
    )

    return ok(
        data=ImportJobOut.model_validate(job).model_dump(mode="json"),
        message="File uploaded. Processing in background — poll preview endpoint until status=ready.",
    )


async def _run_processing(job_id: UUID, content: bytes) -> None:
    """Background task: open a fresh DB session and run the LLM pipeline."""
    from app.core.database import AsyncSessionLocal

    short = str(job_id)[:8]
    logger.info(
        "[job=%s] _run_processing: background task entered bytes=%d",
        short, len(content),
    )
    t0 = time.perf_counter()
    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                job = await db.get(ImportJob, job_id)
                if job is None:
                    logger.warning(
                        "[job=%s] _run_processing: job row not found in DB — aborting",
                        short,
                    )
                    return
                await ImportService().process_job(db, job, content)
        logger.info(
            "[job=%s] _run_processing: background task finished total_ms=%.0f",
            short, (time.perf_counter() - t0) * 1000,
        )
    except Exception:
        # process_job is supposed to swallow & persist its own failures, but
        # log any escaped exception so the background task itself is debuggable.
        logger.exception(
            "[job=%s] _run_processing: unhandled exception after total_ms=%.0f",
            short, (time.perf_counter() - t0) * 1000,
        )


@router.get(
    "/import-jobs/{job_id}",
    dependencies=[Depends(require_roles(UserRole.SUPER_ADMIN))],
)
async def preview_import_job(
    job_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    """
    Poll this endpoint after upload.
    - status=processing → LLM still running, try again shortly
    - status=ready → structured_rows contains the extracted rows for user review/edit
    - status=failed → error_message contains the reason
    """
    job = await db.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")
    if job.uploaded_by != current.id:
        logger.warning(
            "preview_import_job: forbidden — user=%s tried to access job=%s owned by %s",
            str(current.id)[:8], str(job_id)[:8], str(job.uploaded_by)[:8],
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your import job")
    logger.debug(
        "preview_import_job: job=%s status=%s rows=%s",
        str(job_id)[:8],
        getattr(job.status, "value", str(job.status)),
        job.total_rows,
    )
    payload = ImportJobPreviewOut.model_validate(job).model_dump(mode="json")
    return ok(data=payload)


@router.post(
    "/import-jobs/{job_id}/commit",
    dependencies=[Depends(require_roles(UserRole.SUPER_ADMIN))],
)
async def commit_import_job(
    job_id: UUID,
    body: ImportJobCommitBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> dict:
    """
    Commit user-confirmed rows into secondary_sales.
    Send back the structured_rows from preview (possibly edited by the user).
    Rows with skip=true or is_valid=false are excluded automatically.
    """
    short = str(job_id)[:8]
    logger.info(
        "commit_import_job: request job=%s confirmed_rows=%d user=%s",
        short, len(body.confirmed_rows), str(current.id)[:8],
    )
    t0 = time.perf_counter()

    job = await db.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")
    if job.uploaded_by != current.id:
        logger.warning(
            "commit_import_job: forbidden — user=%s job=%s owned by %s",
            str(current.id)[:8], short, str(job.uploaded_by)[:8],
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your import job")
    if job.status not in (ImportJobStatus.ready, ImportJobStatus.partial):
        logger.warning(
            "commit_import_job: rejected — job=%s not committable (status=%s)",
            short, job.status,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not ready for commit (status={job.status})",
        )

    try:
        summary = await ImportService().commit_job(db, job, body.confirmed_rows, current)
    except Exception as e:
        logger.exception(
            "commit_import_job: failed job=%s after %.0fms (%s: %s)",
            short, (time.perf_counter() - t0) * 1000, type(e).__name__, e,
        )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    await db.commit()
    logger.info(
        "commit_import_job: ok job=%s committed=%d skipped=%d total=%d total_ms=%.0f",
        short, summary["committed"], summary["skipped"], summary["total"],
        (time.perf_counter() - t0) * 1000,
    )

    committed = summary["committed"]
    skipped = summary["skipped"]
    if committed == 0:
        message = (
            f"No sales were committed. {skipped} row(s) skipped — "
            f"see skipped_rows for per-row reasons."
        )
    elif skipped > 0:
        message = f"{committed} sale(s) committed, {skipped} skipped — see skipped_rows."
    else:
        message = f"{committed} sale(s) committed successfully."

    return ok(
        data={"job_id": str(job_id), **summary},
        message=message,
    )
