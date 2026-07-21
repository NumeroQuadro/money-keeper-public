from __future__ import annotations

import logging
from typing import List

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ExceptionItem, ImportBatch, ImportFile
from ..schemas import (
    DeleteImportBatchOut,
    DeleteImportFileOut,
    ImportBatchOut,
    ImportFileOut,
    PurgeDataOut,
)
from ..config import settings
from ..services.importer import is_pdf, save_upload
from ..services.import_processing import process_import_batch
from ..services.cleanup import delete_import_batch_data, delete_import_file_data, purge_all_data
from ..services.transfers import detect_transfer_links_in_session

from pathlib import Path

router = APIRouter(prefix="/imports", tags=["imports"])
logger = logging.getLogger(__name__)


def _safe_delete_upload_file(path: str) -> bool:
    if not path:
        return False
    try:
        uploads_root = Path(settings.uploads_dir).resolve()
        candidate = Path(path).resolve()
        candidate.relative_to(uploads_root)
        if candidate.is_file():
            candidate.unlink()
            return True
    except Exception:
        logger.warning("Failed to delete upload file '%s'", path, exc_info=True)
        return False
    return False


@router.get("/batches", response_model=List[ImportBatchOut])
def list_import_batches(
    limit: int = 20,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    return (
        db.query(ImportBatch)
        .options(selectinload(ImportBatch.files))
        .order_by(ImportBatch.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/batches/{batch_id}", response_model=ImportBatchOut)
def get_import_batch(
    batch_id: str,
    db: Session = Depends(get_db),
):
    batch = (
        db.query(ImportBatch)
        .options(selectinload(ImportBatch.files))
        .filter(ImportBatch.id == batch_id)
        .first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Import batch not found")
    return batch


@router.post("/batches/{batch_id}/process", response_model=ImportBatchOut)
def process_import_batch_endpoint(
    batch_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    batch = (
        db.query(ImportBatch)
        .options(selectinload(ImportBatch.files))
        .filter(ImportBatch.id == batch_id)
        .first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Import batch not found")
    background.add_task(process_import_batch, batch.id)
    return batch


@router.post("/batches/{batch_id}/reprocess", response_model=ImportBatchOut)
def reprocess_import_batch_endpoint(
    batch_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    batch = (
        db.query(ImportBatch)
        .options(selectinload(ImportBatch.files))
        .filter(ImportBatch.id == batch_id)
        .first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Import batch not found")

    for item in batch.files:
        if item.status == "failed":
            item.status = "queued"
            item.error_message = ""
    if batch.status == "failed":
        batch.status = "queued"
    db.commit()
    background.add_task(process_import_batch, batch.id)
    db.refresh(batch)
    return batch


@router.post("/pdf", response_model=ImportBatchOut)
async def upload_pdfs(
    background: BackgroundTasks,
    source: str = "api",
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    batch = ImportBatch(source=source, status="received")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    created = 0
    duplicates = 0
    failed = 0
    seen_hashes: set[str] = set()

    for upload in files:
        if not is_pdf(upload):
            failed += 1
            item = ImportFile(
                batch_id=batch.id,
                file_name=upload.filename or "unknown",
                file_path="",
                file_hash="",
                status="failed",
                error_message="Not a PDF",
            )
            db.add(item)
            db.flush()
            db.add(
                ExceptionItem(
                    exception_type="parsing_anomaly",
                    severity="high",
                    status="open",
                    entity_type="import_file",
                    entity_id=item.id,
                    rationale="Uploaded file is not a PDF",
                )
            )
            continue

        file_path, file_hash = save_upload(upload, settings.uploads_dir)
        existing = db.query(ImportFile).filter(ImportFile.file_hash == file_hash).first()
        if existing or file_hash in seen_hashes:
            duplicates += 1
            _safe_delete_upload_file(file_path)
            item = ImportFile(
                batch_id=batch.id,
                file_name=upload.filename or "statement.pdf",
                file_path="",
                file_hash=file_hash,
                status="duplicate",
                error_message="Duplicate upload detected",
            )
            db.add(item)
            seen_hashes.add(file_hash)
            continue

        created += 1
        item = ImportFile(
            batch_id=batch.id,
            file_name=upload.filename or "statement.pdf",
            file_path=file_path,
            file_hash=file_hash,
            status="queued",
        )
        db.add(item)
        seen_hashes.add(file_hash)

    batch.summary = {
        "files_received": len(files),
        "files_queued": created,
        "duplicates": duplicates,
        "failed": failed,
    }
    batch.status = "queued" if created else "failed"
    db.commit()
    db.refresh(batch)

    if created and background is not None:
        # Best-effort async processing: parse PDFs into statements/transactions after the response.
        # This keeps the Telegram bot responsive even for multi-file uploads.
        background.add_task(process_import_batch, batch.id)
    return batch


@router.get("/files", response_model=list[ImportFileOut])
def list_import_files(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return (
        db.query(ImportFile)
        .order_by(ImportFile.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.delete("/files/{file_id}", response_model=DeleteImportFileOut)
def delete_import_file(file_id: str, db: Session = Depends(get_db)):
    exists = db.query(ImportFile.id).filter(ImportFile.id == file_id).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Import file not found")

    result = delete_import_file_data(db, file_id=file_id)
    db.commit()
    try:
        detect_transfer_links_in_session(db)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Transfer relink failed after deleting import file '%s'.",
            file_id,
            exc_info=True,
        )

    deleted_disk_file = _safe_delete_upload_file(result.file_path)
    return DeleteImportFileOut(
        file_id=result.file_id,
        batch_id=result.batch_id,
        deleted_statements=result.deleted_statements,
        deleted_statement_rows=result.deleted_statement_rows,
        deleted_transactions=result.deleted_transactions,
        deleted_transfer_links=result.deleted_transfer_links,
        deleted_balance_snapshots=result.deleted_balance_snapshots,
        deleted_exceptions=result.deleted_exceptions,
        deleted_import_batch=result.deleted_import_batch,
        deleted_disk_file=deleted_disk_file,
    )


@router.delete("/batches/{batch_id}", response_model=DeleteImportBatchOut)
def delete_import_batch(batch_id: str, db: Session = Depends(get_db)):
    exists = db.query(ImportBatch.id).filter(ImportBatch.id == batch_id).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Import batch not found")

    result = delete_import_batch_data(db, batch_id=batch_id)
    db.commit()
    try:
        detect_transfer_links_in_session(db)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Transfer relink failed after deleting import batch '%s'.",
            batch_id,
            exc_info=True,
        )

    deleted_disk_files = 0
    for path in result.file_paths:
        if _safe_delete_upload_file(path):
            deleted_disk_files += 1

    return DeleteImportBatchOut(
        batch_id=result.batch_id,
        deleted_import_files=result.deleted_import_files,
        deleted_statements=result.deleted_statements,
        deleted_statement_rows=result.deleted_statement_rows,
        deleted_transactions=result.deleted_transactions,
        deleted_transfer_links=result.deleted_transfer_links,
        deleted_balance_snapshots=result.deleted_balance_snapshots,
        deleted_exceptions=result.deleted_exceptions,
        deleted_disk_files=deleted_disk_files,
    )


@router.post("/purge", response_model=PurgeDataOut)
def purge_data(confirm: str = Body(..., embed=True), db: Session = Depends(get_db)):
    if confirm != "delete-all":
        raise HTTPException(status_code=400, detail="Invalid confirmation token")

    result = purge_all_data(db)
    db.commit()

    deleted_disk_files = 0
    for path in result.file_paths:
        if _safe_delete_upload_file(path):
            deleted_disk_files += 1

    try:
        uploads_root = Path(settings.uploads_dir).resolve()
        if uploads_root.exists():
            for item in uploads_root.iterdir():
                if item.is_file() and _safe_delete_upload_file(str(item)):
                    deleted_disk_files += 1
    except Exception:
        logger.warning(
            "Failed to cleanup residual upload files after purge in '%s'.",
            settings.uploads_dir,
            exc_info=True,
        )

    return PurgeDataOut(
        deleted_import_batches=result.deleted_import_batches,
        deleted_import_files=result.deleted_import_files,
        deleted_statements=result.deleted_statements,
        deleted_statement_rows=result.deleted_statement_rows,
        deleted_transactions=result.deleted_transactions,
        deleted_transfer_links=result.deleted_transfer_links,
        deleted_balance_snapshots=result.deleted_balance_snapshots,
        deleted_exceptions=result.deleted_exceptions,
        deleted_rules=result.deleted_rules,
        deleted_accounts=result.deleted_accounts,
        deleted_disk_files=deleted_disk_files,
    )
