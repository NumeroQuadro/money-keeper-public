from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    Account,
    BalanceSnapshot,
    ExceptionItem,
    Statement,
    StatementRow,
    Transaction,
    TransferLink,
    transaction_statement_link,
)
from app.services.net_worth import (
    EXCEPTION_TYPE_RECONCILIATION_MISMATCH,
    BALANCE_SNAPSHOT_METHOD_CLOSING,
    BALANCE_SNAPSHOT_METHOD_OPENING,
    rebuild_net_worth_artifacts_in_session,
    compute_net_worth_current,
    compute_net_worth_timeline,
)
from app.tests.db_test_utils import get_test_engine


class NetWorthPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_rebuild_links_accounts_creates_snapshots_and_exception(self) -> None:
        with self._Session() as db:
            statement = Statement(
                provider="ozon",
                account_id=None,
                account_display="1234567890",
                statement_type="card",
                period_start=datetime(2026, 1, 1),
                period_end=datetime(2026, 1, 31),
                generated_at=datetime(2026, 2, 1),
                currency="RUB",
                opening_balance=1000,
                closing_balance=1190,
                total_credits=500,
                total_debits=300,
                parse_confidence=0.85,
                reconcile_status="mismatch",
                pdf_path="/tmp/test.pdf",
            )
            db.add(statement)
            db.commit()

            result = rebuild_net_worth_artifacts_in_session(db)
            db.commit()

            self.assertEqual(result.statements_scanned, 1)
            self.assertEqual(db.query(Account).count(), 1)

            refreshed = db.query(Statement).filter(Statement.id == statement.id).first()
            assert refreshed is not None
            self.assertIsNotNone(refreshed.account_id)

            snapshots = db.query(BalanceSnapshot).all()
            self.assertEqual(len(snapshots), 2)

            exceptions = (
                db.query(ExceptionItem)
                .filter(ExceptionItem.exception_type == EXCEPTION_TYPE_RECONCILIATION_MISMATCH)
                .all()
            )
            self.assertEqual(len(exceptions), 1)

    def test_current_and_timeline_outputs_group_by_currency(self) -> None:
        with self._Session() as db:
            statement = Statement(
                provider="ozon",
                account_id=None,
                account_display="1234567890",
                statement_type="card",
                period_start=datetime(2026, 1, 1),
                period_end=datetime(2026, 1, 31),
                currency="RUB",
                opening_balance=1000,
                closing_balance=1100,
                total_credits=200,
                total_debits=100,
                parse_confidence=0.9,
                reconcile_status="ok",
                pdf_path="/tmp/test.pdf",
            )
            db.add(statement)
            db.commit()

            rebuild_net_worth_artifacts_in_session(db)
            db.commit()

            current = compute_net_worth_current(db)
            self.assertEqual(current["totals"], [{"currency": "RUB", "total_balance": 1100.0}])
            self.assertEqual(len(current["accounts"]), 1)

            timeline = compute_net_worth_timeline(db)
            self.assertEqual(timeline["granularity"], "raw")
            self.assertEqual(len(timeline["series"]), 1)
            self.assertEqual(timeline["series"][0]["currency"], "RUB")
            points = timeline["series"][0]["points"]
            self.assertEqual([p["total_balance"] for p in points], [1000.0, 1100.0])
            self.assertEqual(points[0]["accounts_total"], 1)
            self.assertEqual(points[0]["accounts_with_snapshot"], 1)
            self.assertEqual(points[0]["accounts_missing"], 0)

            daily = compute_net_worth_timeline(db, granularity="day")
            self.assertEqual(daily["granularity"], "day")
            daily_points = daily["series"][0]["points"]
            self.assertGreaterEqual(len(daily_points), 1)
            self.assertIn("completeness", daily_points[0])

    def test_snapshot_priority_for_same_timestamp(self) -> None:
        with self._Session() as db:
            account = Account(
                provider="ozon",
                account_type="card",
                display_name="1234567890",
                currency="RUB",
                include_in_net_worth=True,
            )
            db.add(account)
            db.commit()

            ts = datetime(2026, 1, 15, 12, 0, 0)
            opening = BalanceSnapshot(
                account_id=account.id,
                timestamp=ts,
                balance=1000,
                method=BALANCE_SNAPSHOT_METHOD_OPENING,
                confidence=1.0,
                statement_id=None,
            )
            closing = BalanceSnapshot(
                account_id=account.id,
                timestamp=ts,
                balance=1500,
                method=BALANCE_SNAPSHOT_METHOD_CLOSING,
                confidence=1.0,
                statement_id=None,
            )
            db.add_all([opening, closing])
            db.commit()

            current = compute_net_worth_current(db)
            self.assertEqual(current["totals"], [{"currency": "RUB", "total_balance": 1500.0}])

            timeline = compute_net_worth_timeline(db, granularity="raw")
            points = timeline["series"][0]["points"]
            self.assertEqual(len(points), 1)
            self.assertEqual(points[0]["total_balance"], 1500.0)

    def test_rebuild_realigns_statement_transactions_to_statement_account(self) -> None:
        with self._Session() as db:
            canonical = Account(
                provider="ozon",
                account_type="card",
                display_name="40817810000000000001",
                currency="RUB",
                include_in_net_worth=True,
            )
            db.add(canonical)
            db.commit()

            orphan = Account(
                provider="ozon",
                account_type="card",
                display_name="40817810000000000001",
                currency="RUB",
                include_in_net_worth=True,
            )
            db.add(orphan)
            db.commit()

            statement = Statement(
                provider="ozon",
                account_id=canonical.id,
                account_display="40817810000000000001",
                statement_type="card",
                period_start=datetime(2026, 1, 9),
                period_end=datetime(2026, 2, 9),
                currency="RUB",
                reconcile_status="ok",
                pdf_path="/tmp/ozon.pdf",
            )
            db.add(statement)
            db.flush()

            row = StatementRow(
                statement_id=statement.id,
                row_index=1,
                page_number=1,
                raw_text="05.02.2026 15:08:48 salary +143700",
                raw_data={"provider": "ozon"},
                amount=143700.0,
                currency="RUB",
                direction="in",
                operation_date=datetime(2026, 2, 5, 15, 8, 48),
                posting_date=datetime(2026, 2, 5, 15, 8, 48),
            )
            db.add(row)
            db.flush()

            tx = Transaction(
                account_id=orphan.id,
                dedup_key="legacy-orphan",
                amount=143700.0,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 2, 5, 15, 8, 48),
                posting_datetime=datetime(2026, 2, 5, 15, 8, 48),
                description_raw="ПЛАТ.ВЕД. 102 salary",
                bank_reference_id="182",
                bank_category="",
                meaning="unknown",
                review_status="needs_review",
                source_statement_id=statement.id,
            )
            db.add(tx)
            db.flush()
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx.id,
                    statement_row_id=row.id,
                )
            )
            db.add(
                BalanceSnapshot(
                    account_id=orphan.id,
                    timestamp=datetime(2026, 2, 9, 0, 0, 0),
                    balance=6046.41,
                    method=BALANCE_SNAPSHOT_METHOD_CLOSING,
                    confidence=0.85,
                    statement_id=statement.id,
                )
            )
            db.commit()

            rebuild_net_worth_artifacts_in_session(db)
            db.commit()

            refreshed = db.query(Transaction).filter(Transaction.id == tx.id).first()
            assert refreshed is not None
            self.assertEqual(refreshed.account_id, canonical.id)
            self.assertNotEqual(refreshed.dedup_key, "legacy-orphan")
            snapshot = (
                db.query(BalanceSnapshot)
                .filter(BalanceSnapshot.statement_id == statement.id)
                .filter(BalanceSnapshot.method == BALANCE_SNAPSHOT_METHOD_CLOSING)
                .first()
            )
            assert snapshot is not None
            self.assertEqual(snapshot.account_id, canonical.id)
            self.assertIsNone(db.query(Account).filter(Account.id == orphan.id).first())

    def test_rebuild_merges_duplicate_transactions_and_repoints_transfer_links(self) -> None:
        with self._Session() as db:
            canonical = Account(
                provider="ozon",
                account_type="card",
                display_name="40817810000000000001",
                currency="RUB",
                include_in_net_worth=True,
            )
            db.add(canonical)
            db.commit()

            orphan = Account(
                provider="ozon",
                account_type="card",
                display_name="40817810000000000001",
                currency="RUB",
                include_in_net_worth=True,
            )
            db.add(orphan)
            db.commit()

            counterparty = Account(
                provider="sber",
                account_type="card",
                display_name="4276000000000000",
                currency="RUB",
                include_in_net_worth=True,
            )
            db.add(counterparty)
            db.commit()

            statement = Statement(
                provider="ozon",
                account_id=canonical.id,
                account_display="40817810000000000001",
                statement_type="card",
                period_start=datetime(2026, 1, 9),
                period_end=datetime(2026, 2, 9),
                currency="RUB",
                reconcile_status="ok",
                pdf_path="/tmp/ozon.pdf",
            )
            db.add(statement)
            db.flush()

            row = StatementRow(
                statement_id=statement.id,
                row_index=1,
                page_number=1,
                raw_text="05.02.2026 15:08:48 salary +143700",
                raw_data={"provider": "ozon"},
                amount=143700.0,
                currency="RUB",
                direction="in",
                operation_date=datetime(2026, 2, 5, 15, 8, 48),
                posting_date=datetime(2026, 2, 5, 15, 8, 48),
            )
            db.add(row)
            db.flush()

            canonical_tx = Transaction(
                account_id=canonical.id,
                dedup_key="canonical-key",
                amount=143700.0,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 2, 5, 15, 8, 48),
                posting_datetime=datetime(2026, 2, 5, 15, 8, 48),
                description_raw="ПЛАТ.ВЕД. 102 salary",
                bank_reference_id="182",
                bank_category="",
                meaning="unknown",
                review_status="needs_review",
                source_statement_id="other-statement",
            )
            orphan_tx = Transaction(
                account_id=orphan.id,
                dedup_key="orphan-key",
                amount=143700.0,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 2, 5, 15, 8, 48),
                posting_datetime=datetime(2026, 2, 5, 15, 8, 48),
                description_raw="ПЛАТ.ВЕД. 102 salary",
                bank_reference_id="182",
                bank_category="",
                meaning="unknown",
                review_status="needs_review",
                source_statement_id=statement.id,
            )
            out_tx = Transaction(
                account_id=counterparty.id,
                dedup_key="out-key",
                amount=143000.0,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 2, 5, 15, 9, 42),
                description_raw="Перевод собственных средств",
                bank_reference_id="xfer",
                bank_category="transfer",
                meaning="internal_transfer",
                review_status="needs_review",
            )
            db.add_all([canonical_tx, orphan_tx, out_tx])
            db.flush()
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=orphan_tx.id,
                    statement_row_id=row.id,
                )
            )
            db.add(
                TransferLink(
                    transaction_out_id=out_tx.id,
                    transaction_in_id=orphan_tx.id,
                    status="suggested",
                    match_score=0.91,
                    rationale="candidate transfer",
                )
            )
            db.commit()

            rebuild_net_worth_artifacts_in_session(db)
            db.commit()

            self.assertEqual(
                db.query(Transaction)
                .filter(Transaction.description_raw == "ПЛАТ.ВЕД. 102 salary")
                .count(),
                1,
            )

            merged = db.query(Transaction).filter(Transaction.id == canonical_tx.id).first()
            assert merged is not None
            self.assertEqual(merged.account_id, canonical.id)
            self.assertEqual(len(merged.statement_rows), 1)

            transfer = db.query(TransferLink).first()
            assert transfer is not None
            self.assertEqual(transfer.transaction_in_id, canonical_tx.id)


if __name__ == "__main__":
    unittest.main()
