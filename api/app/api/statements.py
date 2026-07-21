from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Statement, StatementRow
from ..schemas import DeleteStatementOut, StatementOut, StatementRowOut
from ..services.cleanup import delete_statement_data
from ..services.transfers import detect_transfer_links_in_session

router = APIRouter(prefix="/statements", tags=["statements"])


@router.get("/", response_model=list[StatementOut])
def list_statements(db: Session = Depends(get_db)):
    return db.query(Statement).order_by(Statement.created_at.desc()).all()


@router.get("/{statement_id}/rows", response_model=list[StatementRowOut])
def list_statement_rows(
    statement_id: str,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    exists = db.query(Statement.id).filter(Statement.id == statement_id).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Statement not found")
    return (
        db.query(StatementRow)
        .filter(StatementRow.statement_id == statement_id)
        .order_by(StatementRow.row_index.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.delete("/{statement_id}", response_model=DeleteStatementOut)
def delete_statement(statement_id: str, db: Session = Depends(get_db)):
    exists = db.query(Statement.id).filter(Statement.id == statement_id).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Statement not found")

    result = delete_statement_data(db, statement_id=statement_id)
    db.commit()
    try:
        detect_transfer_links_in_session(db)
        db.commit()
    except Exception:
        db.rollback()

    return DeleteStatementOut(
        statement_id=result.statement_id,
        deleted_statement_rows=result.deleted_statement_rows,
        deleted_transactions=result.deleted_transactions,
        deleted_transfer_links=result.deleted_transfer_links,
        deleted_balance_snapshots=result.deleted_balance_snapshots,
        deleted_exceptions=result.deleted_exceptions,
    )
