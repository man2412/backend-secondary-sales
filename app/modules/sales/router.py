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

@router.post("/import-jobs", dependencies=[Depends(require_roles(UserRole.SUPER_ADMIN))])
async def upload_import_job(
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(description="Upload .pdf, .xlsx, .xls, or .csv")],
    mr_id: Annotated[UUID | None, Query(description="MR this import belongs to (optional if FOS name is in file)")] = None,
) -> dict:
    """
    Upload a distributor sales file. Processing (LLM parse + validation) runs in the background.
    Poll GET /import-jobs/{id}/preview until status = ready.
    """
    content = await file.read()
    filename = file.filename or "upload"

    try:
        job = await ImportService().create_job(
            db,
            filename=filename,
            content=content,
            uploaded_by=current.id,
            mr_id=mr_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Commit the job row first, then process in background
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(_run_processing, job.id, content)

    return ok(
        data=ImportJobOut.model_validate(job).model_dump(mode="json"),
        message="File uploaded. Processing in background — poll preview endpoint until status=ready.",
    )


async def _run_processing(job_id: UUID, content: bytes) -> None:
    """Background task: open a fresh DB session and run the LLM pipeline."""
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        async with db.begin():
            job = await db.get(ImportJob, job_id)
            if job is None:
                return
            await ImportService().process_job(db, job, content)


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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your import job")
    return ok(data=ImportJobPreviewOut.model_validate(job).model_dump(mode="json"))


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
    job = await db.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")
    if job.uploaded_by != current.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your import job")
    if job.status not in (ImportJobStatus.ready, ImportJobStatus.committed):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not ready for commit (status={job.status})",
        )

    try:
        committed = await ImportService().commit_job(db, job, body.confirmed_rows, current)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    await db.commit()
    return ok(
        data={"committed": committed, "job_id": str(job_id), "status": job.status},
        message=f"{committed} sale(s) committed successfully.",
    )
