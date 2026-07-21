from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .db import Base


class Provider(str, enum.Enum):
    ozon = "ozon"
    sber = "sber"
    yandex = "yandex"
    spb = "spb"
    unknown = "unknown"


class AccountType(str, enum.Enum):
    card = "card"
    payment = "payment"
    savings = "savings"
    deposit = "deposit"
    wallet = "wallet"
    unknown = "unknown"


class TransactionDirection(str, enum.Enum):
    inflow = "in"
    outflow = "out"


class TransactionMeaning(str, enum.Enum):
    spend = "spend"
    income = "income"
    internal_transfer = "internal_transfer"
    external_transfer = "external_transfer"
    fee = "fee"
    refund = "refund"
    cashback = "cashback"
    interest = "interest"
    adjustment = "adjustment"
    unknown = "unknown"


class TransferStatus(str, enum.Enum):
    auto = "auto"
    suggested = "suggested"
    confirmed = "confirmed"
    rejected = "rejected"


class ReviewStatus(str, enum.Enum):
    reviewed = "reviewed"
    needs_review = "needs_review"


class ExceptionStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
    ignored = "ignored"


class ImportStatus(str, enum.Enum):
    received = "received"
    queued = "queued"
    processing = "processing"
    processed = "processed"
    failed = "failed"


transaction_statement_link = Table(
    "transaction_statement_links",
    Base.metadata,
    Column("transaction_id", String, ForeignKey("transactions.id"), primary_key=True),
    Column("statement_row_id", String, ForeignKey("statement_rows.id"), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[str] = mapped_column(String, default=Provider.unknown.value)
    account_type: Mapped[str] = mapped_column(String, default=AccountType.unknown.value)
    display_name: Mapped[str] = mapped_column(String, default="")
    masked_identifier: Mapped[str] = mapped_column(String, default="")
    currency: Mapped[str] = mapped_column(String, default="RUB")
    include_in_net_worth: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    statements: Mapped[list[Statement]] = relationship(
        "Statement", back_populates="account", cascade="all, delete-orphan"
    )
    balance_snapshots: Mapped[list[BalanceSnapshot]] = relationship(
        "BalanceSnapshot", back_populates="account", cascade="all, delete-orphan"
    )


class Statement(Base):
    __tablename__ = "statements"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[str] = mapped_column(String, default=Provider.unknown.value)
    account_id: Mapped[str | None] = mapped_column(String, ForeignKey("accounts.id"), nullable=True)
    account_display: Mapped[str] = mapped_column(String, default="")
    statement_type: Mapped[str] = mapped_column(String, default="unknown")
    period_start: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    currency: Mapped[str] = mapped_column(String, default="RUB")
    opening_balance: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    closing_balance: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_credits: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_debits: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    parse_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    reconcile_status: Mapped[str] = mapped_column(String, default="unknown")
    pdf_path: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped[Account | None] = relationship("Account", back_populates="statements")
    rows: Mapped[list[StatementRow]] = relationship(
        "StatementRow", back_populates="statement", cascade="all, delete-orphan"
    )


class StatementRow(Base):
    __tablename__ = "statement_rows"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    statement_id: Mapped[str] = mapped_column(String, ForeignKey("statements.id"))
    row_index: Mapped[int] = mapped_column(Integer, default=0)
    page_number: Mapped[int] = mapped_column(Integer, default=0)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String, default="RUB")
    direction: Mapped[str] = mapped_column(String, default=TransactionDirection.outflow.value)
    operation_date: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posting_date: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parse_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)

    statement: Mapped[Statement] = relationship("Statement", back_populates="rows")
    transactions: Mapped[list[Transaction]] = relationship(
        "Transaction", secondary=transaction_statement_link, back_populates="statement_rows"
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str | None] = mapped_column(String, ForeignKey("accounts.id"), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String, default="")
    operation_datetime: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posting_datetime: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timestamp_precision: Mapped[str] = mapped_column(String, default="unknown")
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String, default="RUB")
    direction: Mapped[str] = mapped_column(String, default=TransactionDirection.outflow.value)
    description_raw: Mapped[str] = mapped_column(Text, default="")
    merchant_normalized: Mapped[str] = mapped_column(String, default="")
    bank_reference_id: Mapped[str] = mapped_column(String, default="")
    bank_category: Mapped[str] = mapped_column(String, default="")
    meaning: Mapped[str] = mapped_column(String, default=TransactionMeaning.unknown.value)
    meaning_confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    category: Mapped[str] = mapped_column(String, default="")
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    review_status: Mapped[str] = mapped_column(String, default=ReviewStatus.reviewed.value)
    source_statement_id: Mapped[str] = mapped_column(String, default="")
    source_page_number: Mapped[int] = mapped_column(Integer, default=0)
    source_row_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    statement_rows: Mapped[list[StatementRow]] = relationship(
        "StatementRow", secondary=transaction_statement_link, back_populates="transactions"
    )
    account: Mapped[Account | None] = relationship("Account")


class TransferLink(Base):
    __tablename__ = "transfer_links"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    transaction_out_id: Mapped[str] = mapped_column(String, ForeignKey("transactions.id"))
    transaction_in_id: Mapped[str] = mapped_column(String, ForeignKey("transactions.id"))
    status: Mapped[str] = mapped_column(String, default=TransferStatus.suggested.value)
    match_score: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    fee_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, default="")
    pattern: Mapped[str] = mapped_column(String, default="")
    conditions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ExceptionItem(Base):
    __tablename__ = "exceptions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    exception_type: Mapped[str] = mapped_column(String, default="unknown")
    severity: Mapped[str] = mapped_column(String, default="medium")
    status: Mapped[str] = mapped_column(String, default=ExceptionStatus.open.value)
    entity_type: Mapped[str] = mapped_column(String, default="")
    entity_id: Mapped[str] = mapped_column(String, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"))
    timestamp: Mapped[str] = mapped_column(DateTime(timezone=True))
    balance: Mapped[float] = mapped_column(Numeric(14, 2))
    method: Mapped[str] = mapped_column(String, default="unknown")
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    statement_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("statements.id"), nullable=True
    )

    account: Mapped[Account] = relationship("Account", back_populates="balance_snapshots")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[str] = mapped_column(String, default="unknown")
    status: Mapped[str] = mapped_column(String, default=ImportStatus.received.value)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    files: Mapped[list[ImportFile]] = relationship(
        "ImportFile", back_populates="batch", cascade="all, delete-orphan"
    )


class ImportFile(Base):
    __tablename__ = "import_files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id: Mapped[str] = mapped_column(String, ForeignKey("import_batches.id"))
    file_name: Mapped[str] = mapped_column(String, default="")
    file_path: Mapped[str] = mapped_column(String, default="")
    file_hash: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default=ImportStatus.received.value)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped[ImportBatch] = relationship("ImportBatch", back_populates="files")


Index("ix_transactions_operation_datetime", Transaction.operation_datetime)
Index("ix_transactions_timestamp_precision", Transaction.timestamp_precision)
Index("ix_transactions_description", Transaction.description_raw)
Index("ix_transactions_merchant", Transaction.merchant_normalized)
Index("ix_transactions_account", Transaction.account_id)
Index(
    "ix_transactions_source_ordering",
    Transaction.source_statement_id,
    Transaction.source_page_number,
    Transaction.source_row_index,
)
Index(
    "ux_transactions_account_dedup_key",
    Transaction.account_id,
    Transaction.dedup_key,
    unique=True,
    postgresql_where=text("dedup_key <> ''"),
)
Index(
    "ix_balance_snapshots_account_timestamp", BalanceSnapshot.account_id, BalanceSnapshot.timestamp
)
Index("ix_balance_snapshots_statement_id", BalanceSnapshot.statement_id)
Index("ix_import_files_batch_id", ImportFile.batch_id)
Index("ix_statement_rows_statement_id", StatementRow.statement_id)
Index("ix_statements_account_id", Statement.account_id)
Index("ix_transfer_links_transaction_in_id", TransferLink.transaction_in_id)
Index("ix_transfer_links_transaction_out_id", TransferLink.transaction_out_id)
Index("ix_transfer_links_status", TransferLink.status)
Index(
    "ix_transaction_statement_links_statement_row_id",
    transaction_statement_link.c.statement_row_id,
)
Index(
    "ix_transaction_statement_links_transaction_id",
    transaction_statement_link.c.transaction_id,
)
