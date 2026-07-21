from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from app.api.transfers import list_transfer_links
from app.db import Base
from app.models import (
    Account,
    Statement,
    StatementRow,
    Transaction,
    TransferLink,
    transaction_statement_link,
)
from app.services.transfers import (
    confirm_transfer_link,
    detect_transfer_links_in_session,
    reject_transfer_link,
)
from app.tests.db_test_utils import get_test_engine


class TransferDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        self._row_index = 0

    def _create_account_statement(self, db, *, provider: str, account_type: str, display_name: str):
        account = Account(provider=provider, account_type=account_type, display_name=display_name)
        db.add(account)
        db.flush()
        statement = Statement(
            provider=provider,
            account_id=account.id,
            account_display=display_name,
            currency="RUB",
        )
        db.add(statement)
        db.flush()
        return account, statement

    def _create_statement_transaction(
        self,
        db,
        *,
        statement: Statement,
        account_id: str,
        amount: float,
        direction: str,
        operation_datetime: datetime,
        description_raw: str,
        bank_category: str = "",
        bank_reference_id: str = "",
    ) -> Transaction:
        self._row_index += 1
        row = StatementRow(
            statement_id=statement.id,
            row_index=self._row_index,
            page_number=1,
            raw_text="r",
            raw_data={},
        )
        db.add(row)
        db.flush()

        tx = Transaction(
            account_id=account_id,
            amount=amount,
            currency="RUB",
            direction=direction,
            operation_datetime=operation_datetime,
            description_raw=description_raw,
            bank_category=bank_category,
            bank_reference_id=bank_reference_id,
            meaning="unknown",
        )
        db.add(tx)
        db.flush()
        db.execute(
            transaction_statement_link.insert().values(
                transaction_id=tx.id, statement_row_id=row.id
            )
        )
        return tx

    def _add_confirmed_link(
        self, db, *, tx_out: Transaction, tx_in: Transaction, score: float = 0.96
    ) -> None:
        db.add(
            TransferLink(
                transaction_out_id=tx_out.id,
                transaction_in_id=tx_in.id,
                status="confirmed",
                match_score=score,
                rationale="manual-confirmed",
            )
        )

    def test_detects_auto_link_and_sets_internal_transfer_meaning(self) -> None:
        with self._Session() as db:
            acc_out = Account(provider="ozon", account_type="card", display_name="A")
            acc_in = Account(provider="sber", account_type="savings", display_name="B")
            db.add_all([acc_out, acc_in])
            db.flush()

            st_out = Statement(
                provider="ozon", account_id=acc_out.id, account_display="A", currency="RUB"
            )
            st_in = Statement(
                provider="sber", account_id=acc_in.id, account_display="B", currency="RUB"
            )
            db.add_all([st_out, st_in])
            db.flush()

            row_out = StatementRow(
                statement_id=st_out.id, row_index=0, page_number=1, raw_text="r", raw_data={}
            )
            row_in = StatementRow(
                statement_id=st_in.id, row_index=0, page_number=1, raw_text="r", raw_data={}
            )
            db.add_all([row_out, row_in])
            db.flush()

            t0 = datetime(2026, 1, 10, 12, 0, 0)
            tx_out = Transaction(
                amount=100.00,
                currency="RUB",
                direction="out",
                operation_datetime=t0,
                description_raw="Transfer between accounts",
                meaning="unknown",
            )
            tx_in = Transaction(
                amount=100.00,
                currency="RUB",
                direction="in",
                operation_datetime=t0 + timedelta(minutes=3),
                description_raw="Transfer between accounts",
                meaning="unknown",
            )
            db.add_all([tx_out, tx_in])
            db.flush()

            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_out.id, statement_row_id=row_out.id
                )
            )
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_in.id, statement_row_id=row_in.id
                )
            )
            db.commit()

            result = detect_transfer_links_in_session(db)
            db.commit()

            self.assertEqual(result.links_created, 1)
            self.assertEqual(result.auto_links_created, 1)
            self.assertEqual(db.query(TransferLink).count(), 1)

            refreshed_out = db.query(Transaction).filter(Transaction.id == tx_out.id).first()
            refreshed_in = db.query(Transaction).filter(Transaction.id == tx_in.id).first()
            assert refreshed_out is not None
            assert refreshed_in is not None
            self.assertEqual(refreshed_out.meaning, "internal_transfer")
            self.assertEqual(refreshed_in.meaning, "internal_transfer")

            # Idempotent: second run should not create duplicates.
            again = detect_transfer_links_in_session(db)
            db.commit()
            self.assertEqual(again.links_created, 0)
            self.assertEqual(db.query(TransferLink).count(), 1)

    def test_suggested_link_requires_confirmation_to_exclude(self) -> None:
        with self._Session() as db:
            acc_out = Account(provider="ozon", account_type="card", display_name="A")
            acc_in = Account(provider="sber", account_type="card", display_name="B")
            db.add_all([acc_out, acc_in])
            db.flush()

            st_out = Statement(
                provider="ozon", account_id=acc_out.id, account_display="A", currency="RUB"
            )
            st_in = Statement(
                provider="sber", account_id=acc_in.id, account_display="B", currency="RUB"
            )
            db.add_all([st_out, st_in])
            db.flush()

            row_out = StatementRow(
                statement_id=st_out.id, row_index=0, page_number=1, raw_text="r", raw_data={}
            )
            row_in = StatementRow(
                statement_id=st_in.id, row_index=0, page_number=1, raw_text="r", raw_data={}
            )
            db.add_all([row_out, row_in])
            db.flush()

            t0 = datetime(2026, 1, 12, 9, 0, 0)
            tx_out = Transaction(
                amount=500.00,
                currency="RUB",
                direction="out",
                operation_datetime=t0,
                description_raw="Card to card",
                meaning="unknown",
            )
            tx_in = Transaction(
                amount=500.00,
                currency="RUB",
                direction="in",
                operation_datetime=t0 + timedelta(minutes=1),
                description_raw="Incoming credit",
                meaning="unknown",
            )
            db.add_all([tx_out, tx_in])
            db.flush()

            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_out.id, statement_row_id=row_out.id
                )
            )
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_in.id, statement_row_id=row_in.id
                )
            )
            db.commit()

            result = detect_transfer_links_in_session(db)
            db.commit()

            self.assertEqual(result.links_created, 1)
            link = db.query(TransferLink).first()
            assert link is not None
            self.assertEqual(link.status, "suggested")

            refreshed_out = db.query(Transaction).filter(Transaction.id == tx_out.id).first()
            refreshed_in = db.query(Transaction).filter(Transaction.id == tx_in.id).first()
            assert refreshed_out is not None
            assert refreshed_in is not None
            self.assertEqual(refreshed_out.meaning, "unknown")
            self.assertEqual(refreshed_in.meaning, "unknown")

    def test_fee_amount_is_recorded_for_amount_delta(self) -> None:
        with self._Session() as db:
            acc_out = Account(provider="ozon", account_type="card", display_name="A")
            acc_in = Account(provider="sber", account_type="savings", display_name="B")
            db.add_all([acc_out, acc_in])
            db.flush()

            st_out = Statement(
                provider="ozon", account_id=acc_out.id, account_display="A", currency="RUB"
            )
            st_in = Statement(
                provider="sber", account_id=acc_in.id, account_display="B", currency="RUB"
            )
            db.add_all([st_out, st_in])
            db.flush()

            row_out = StatementRow(
                statement_id=st_out.id, row_index=0, page_number=1, raw_text="r", raw_data={}
            )
            row_in = StatementRow(
                statement_id=st_in.id, row_index=0, page_number=1, raw_text="r", raw_data={}
            )
            db.add_all([row_out, row_in])
            db.flush()

            t0 = datetime(2026, 1, 15, 10, 0, 0)
            tx_out = Transaction(
                amount=100.00,
                currency="RUB",
                direction="out",
                operation_datetime=t0,
                description_raw="Transfer between accounts",
                meaning="unknown",
            )
            tx_in = Transaction(
                amount=99.50,
                currency="RUB",
                direction="in",
                operation_datetime=t0 + timedelta(minutes=2),
                description_raw="Transfer between accounts",
                meaning="unknown",
            )
            db.add_all([tx_out, tx_in])
            db.flush()

            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_out.id, statement_row_id=row_out.id
                )
            )
            db.execute(
                transaction_statement_link.insert().values(
                    transaction_id=tx_in.id, statement_row_id=row_in.id
                )
            )
            db.commit()

            result = detect_transfer_links_in_session(db)
            db.commit()

            self.assertEqual(result.links_created, 1)
            link = db.query(TransferLink).first()
            assert link is not None
            self.assertAlmostEqual(float(link.fee_amount or 0.0), 0.5, places=2)

            confirm_transfer_link(db, link=link)
            db.commit()
            self.assertEqual(link.status, "confirmed")

            refreshed_out = db.query(Transaction).filter(Transaction.id == tx_out.id).first()
            refreshed_in = db.query(Transaction).filter(Transaction.id == tx_in.id).first()
            assert refreshed_out is not None
            assert refreshed_in is not None
            self.assertEqual(refreshed_out.meaning, "internal_transfer")
            self.assertEqual(refreshed_in.meaning, "internal_transfer")

            reject_transfer_link(db, link=link)
            db.commit()
            self.assertEqual(link.status, "rejected")

            refreshed_out = db.query(Transaction).filter(Transaction.id == tx_out.id).first()
            refreshed_in = db.query(Transaction).filter(Transaction.id == tx_in.id).first()
            assert refreshed_out is not None
            assert refreshed_in is not None
            self.assertEqual(refreshed_out.meaning, "unknown")
            self.assertEqual(refreshed_in.meaning, "unknown")

    def test_list_transfer_links_orders_by_linked_transaction_datetime(self) -> None:
        with self._Session() as db:
            acc_a = Account(provider="ozon", account_type="card", display_name="A")
            acc_b = Account(provider="sber", account_type="savings", display_name="B")
            acc_c = Account(provider="spb", account_type="card", display_name="C")
            db.add_all([acc_a, acc_b, acc_c])
            db.flush()

            tx_out_old = Transaction(
                account_id=acc_a.id,
                amount=100.00,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 1, 10, 0, 0),
                description_raw="old out",
            )
            tx_in_old = Transaction(
                account_id=acc_b.id,
                amount=100.00,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 1, 10, 5, 0),
                description_raw="old in",
            )

            tx_out_mid = Transaction(
                account_id=acc_a.id,
                amount=200.00,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 2, 8, 0, 0),
                description_raw="mid out",
            )
            tx_in_mid = Transaction(
                account_id=acc_c.id,
                amount=200.00,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 2, 8, 30, 0),
                description_raw="mid in",
            )

            tx_out_new = Transaction(
                account_id=acc_b.id,
                amount=300.00,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 3, 9, 0, 0),
                description_raw="new out",
            )
            tx_in_new = Transaction(
                account_id=acc_c.id,
                amount=300.00,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 3, 9, 10, 0),
                description_raw="new in",
            )
            db.add_all([tx_out_old, tx_in_old, tx_out_mid, tx_in_mid, tx_out_new, tx_in_new])
            db.flush()

            link_old = TransferLink(
                transaction_out_id=tx_out_old.id,
                transaction_in_id=tx_in_old.id,
                status="suggested",
                match_score=0.6,
                rationale="old",
                created_at=datetime(2026, 2, 10, 0, 0, 0),
            )
            link_mid = TransferLink(
                transaction_out_id=tx_out_mid.id,
                transaction_in_id=tx_in_mid.id,
                status="suggested",
                match_score=0.7,
                rationale="mid",
                created_at=datetime(2026, 2, 11, 0, 0, 0),
            )
            link_new = TransferLink(
                transaction_out_id=tx_out_new.id,
                transaction_in_id=tx_in_new.id,
                status="suggested",
                match_score=0.8,
                rationale="new",
                created_at=datetime(2026, 1, 1, 0, 0, 0),
            )
            db.add_all([link_old, link_mid, link_new])
            db.commit()

            links = list_transfer_links(status=None, limit=50, db=db)
            ordered_ids = [item.id for item in links]
            self.assertEqual(ordered_ids, [link_new.id, link_mid.id, link_old.id])

    def test_list_transfer_links_uses_date_only_source_tiebreakers(self) -> None:
        with self._Session() as db:
            acc_a = Account(provider="ozon", account_type="card", display_name="A")
            acc_b = Account(provider="sber", account_type="savings", display_name="B")
            acc_c = Account(provider="spb", account_type="card", display_name="C")
            db.add_all([acc_a, acc_b, acc_c])
            db.flush()

            tx_out_a = Transaction(
                account_id=acc_a.id,
                amount=100.00,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 7, 0, 0, 0),
                timestamp_precision="date_only",
                source_statement_id="st-a",
                source_page_number=1,
                source_row_index=1,
                description_raw="out a",
            )
            tx_in_a = Transaction(
                account_id=acc_b.id,
                amount=100.00,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 7, 0, 0, 0),
                timestamp_precision="date_only",
                source_statement_id="st-a",
                source_page_number=1,
                source_row_index=2,
                description_raw="in a",
            )
            tx_out_b = Transaction(
                account_id=acc_a.id,
                amount=100.00,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 7, 0, 0, 0),
                timestamp_precision="date_only",
                source_statement_id="st-b",
                source_page_number=1,
                source_row_index=1,
                description_raw="out b",
            )
            tx_in_b = Transaction(
                account_id=acc_c.id,
                amount=100.00,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 7, 0, 0, 0),
                timestamp_precision="date_only",
                source_statement_id="st-b",
                source_page_number=1,
                source_row_index=2,
                description_raw="in b",
            )
            db.add_all([tx_out_a, tx_in_a, tx_out_b, tx_in_b])
            db.flush()

            link_a = TransferLink(
                transaction_out_id=tx_out_a.id,
                transaction_in_id=tx_in_a.id,
                status="suggested",
                match_score=0.6,
                rationale="date-only-a",
                created_at=datetime(2026, 1, 8, 0, 0, 0),
            )
            link_b = TransferLink(
                transaction_out_id=tx_out_b.id,
                transaction_in_id=tx_in_b.id,
                status="suggested",
                match_score=0.6,
                rationale="date-only-b",
                created_at=datetime(2026, 1, 8, 0, 0, 0),
            )
            db.add_all([link_a, link_b])
            db.commit()

            links = list_transfer_links(status=None, limit=50, db=db)
            ordered_ids = [item.id for item in links]
            self.assertEqual(ordered_ids, [link_b.id, link_a.id])

    def test_list_transfer_links_status_filter_still_applies(self) -> None:
        with self._Session() as db:
            acc_a = Account(provider="ozon", account_type="card", display_name="A")
            acc_b = Account(provider="sber", account_type="savings", display_name="B")
            db.add_all([acc_a, acc_b])
            db.flush()

            tx_out = Transaction(
                account_id=acc_a.id,
                amount=100.00,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 4, 10, 0, 0),
                description_raw="out",
            )
            tx_in = Transaction(
                account_id=acc_b.id,
                amount=100.00,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 4, 10, 10, 0),
                description_raw="in",
            )
            tx_out_2 = Transaction(
                account_id=acc_a.id,
                amount=200.00,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 10, 0, 0),
                description_raw="out2",
            )
            tx_in_2 = Transaction(
                account_id=acc_b.id,
                amount=200.00,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 5, 10, 10, 0),
                description_raw="in2",
            )
            db.add_all([tx_out, tx_in, tx_out_2, tx_in_2])
            db.flush()

            suggested = TransferLink(
                transaction_out_id=tx_out.id,
                transaction_in_id=tx_in.id,
                status="suggested",
                match_score=0.5,
                rationale="suggested",
            )
            confirmed = TransferLink(
                transaction_out_id=tx_out_2.id,
                transaction_in_id=tx_in_2.id,
                status="confirmed",
                match_score=0.9,
                rationale="confirmed",
            )
            db.add_all([suggested, confirmed])
            db.commit()

            links = list_transfer_links(status="suggested", limit=50, db=db)
            self.assertEqual([item.id for item in links], [suggested.id])

    def test_lane_prior_auto_links_after_repeated_confirmations(self) -> None:
        with self._Session() as db:
            acc_out, st_out = self._create_account_statement(
                db, provider="ozon", account_type="card", display_name="lane-out"
            )
            acc_in, st_in = self._create_account_statement(
                db, provider="sber", account_type="savings", display_name="lane-in"
            )

            baseline = datetime(2026, 1, 1, 9, 0, 0)
            for idx in range(4):
                out_tx = self._create_statement_transaction(
                    db,
                    statement=st_out,
                    account_id=acc_out.id,
                    amount=3500.00,
                    direction="out",
                    operation_datetime=baseline + timedelta(days=idx),
                    description_raw="Перевод на копилка",
                    bank_category="transfer",
                )
                in_tx = self._create_statement_transaction(
                    db,
                    statement=st_in,
                    account_id=acc_in.id,
                    amount=3500.00,
                    direction="in",
                    operation_datetime=baseline + timedelta(days=idx, hours=22),
                    description_raw="Перевод с копилка",
                    bank_category="transfer",
                )
                self._add_confirmed_link(db, tx_out=out_tx, tx_in=in_tx)

            candidate_out = self._create_statement_transaction(
                db,
                statement=st_out,
                account_id=acc_out.id,
                amount=4100.00,
                direction="out",
                operation_datetime=datetime(2026, 2, 10, 10, 0, 0),
                description_raw="Перевод на копилка",
                bank_category="transfer",
            )
            candidate_in = self._create_statement_transaction(
                db,
                statement=st_in,
                account_id=acc_in.id,
                amount=4100.00,
                direction="in",
                operation_datetime=datetime(2026, 2, 11, 9, 0, 0),
                description_raw="Перевод с копилка",
                bank_category="transfer",
            )
            db.commit()

            result = detect_transfer_links_in_session(db)
            db.commit()

            self.assertEqual(result.links_created, 1)
            link = (
                db.query(TransferLink)
                .filter(TransferLink.transaction_out_id == candidate_out.id)
                .filter(TransferLink.transaction_in_id == candidate_in.id)
                .first()
            )
            assert link is not None
            self.assertEqual(link.status, "auto")
            self.assertIn("auto_lane=lane_prior", link.rationale)
            self.assertIn("lane_prior_confirmations=4", link.rationale)

    def test_lane_prior_allows_cross_day_transfer_match_with_booking_drift(self) -> None:
        with self._Session() as db:
            acc_out, st_out = self._create_account_statement(
                db, provider="ozon", account_type="card", display_name="drift-out"
            )
            acc_in, st_in = self._create_account_statement(
                db, provider="sber", account_type="savings", display_name="drift-in"
            )

            baseline = datetime(2026, 1, 5, 8, 0, 0)
            for idx in range(4):
                out_tx = self._create_statement_transaction(
                    db,
                    statement=st_out,
                    account_id=acc_out.id,
                    amount=5000.00,
                    direction="out",
                    operation_datetime=baseline + timedelta(days=idx),
                    description_raw="Перевод на копилка",
                    bank_category="transfer",
                )
                in_tx = self._create_statement_transaction(
                    db,
                    statement=st_in,
                    account_id=acc_in.id,
                    amount=5000.00,
                    direction="in",
                    operation_datetime=baseline + timedelta(days=idx, hours=60),
                    description_raw="Перевод с копилка",
                    bank_category="transfer",
                )
                self._add_confirmed_link(db, tx_out=out_tx, tx_in=in_tx)

            candidate_out = self._create_statement_transaction(
                db,
                statement=st_out,
                account_id=acc_out.id,
                amount=6200.00,
                direction="out",
                operation_datetime=datetime(2026, 2, 12, 10, 0, 0),
                description_raw="Перевод на копилка",
                bank_category="transfer",
            )
            candidate_in = self._create_statement_transaction(
                db,
                statement=st_in,
                account_id=acc_in.id,
                amount=6200.00,
                direction="in",
                operation_datetime=datetime(2026, 2, 15, 10, 0, 0),
                description_raw="Перевод с копилка",
                bank_category="transfer",
            )
            db.commit()

            result = detect_transfer_links_in_session(db)
            db.commit()

            self.assertEqual(result.links_created, 1)
            link = (
                db.query(TransferLink)
                .filter(TransferLink.transaction_out_id == candidate_out.id)
                .filter(TransferLink.transaction_in_id == candidate_in.id)
                .first()
            )
            assert link is not None
            self.assertEqual(link.status, "suggested")
            self.assertIn("lane_prior_confirmations=4", link.rationale)
            self.assertIn("match_window_s=410400", link.rationale)

    def test_lane_prior_does_not_auto_link_merchant_outflow(self) -> None:
        with self._Session() as db:
            acc_out, st_out = self._create_account_statement(
                db, provider="ozon", account_type="card", display_name="merchant-out"
            )
            acc_in, st_in = self._create_account_statement(
                db, provider="sber", account_type="savings", display_name="merchant-in"
            )

            baseline = datetime(2026, 1, 8, 9, 0, 0)
            for idx in range(4):
                out_tx = self._create_statement_transaction(
                    db,
                    statement=st_out,
                    account_id=acc_out.id,
                    amount=2800.00,
                    direction="out",
                    operation_datetime=baseline + timedelta(days=idx),
                    description_raw="Перевод на копилка",
                    bank_category="transfer",
                )
                in_tx = self._create_statement_transaction(
                    db,
                    statement=st_in,
                    account_id=acc_in.id,
                    amount=2800.00,
                    direction="in",
                    operation_datetime=baseline + timedelta(days=idx, hours=8),
                    description_raw="Перевод с копилка",
                    bank_category="transfer",
                )
                self._add_confirmed_link(db, tx_out=out_tx, tx_in=in_tx)

            candidate_out = self._create_statement_transaction(
                db,
                statement=st_out,
                account_id=acc_out.id,
                amount=3100.00,
                direction="out",
                operation_datetime=datetime(2026, 2, 20, 12, 0, 0),
                description_raw="Списание покупка OOO MARKET",
                bank_category="shopping",
            )
            candidate_in = self._create_statement_transaction(
                db,
                statement=st_in,
                account_id=acc_in.id,
                amount=3100.00,
                direction="in",
                operation_datetime=datetime(2026, 2, 20, 13, 0, 0),
                description_raw="Перевод с копилка",
                bank_category="transfer",
            )
            db.commit()

            result = detect_transfer_links_in_session(db)
            db.commit()

            self.assertEqual(result.links_created, 1)
            link = (
                db.query(TransferLink)
                .filter(TransferLink.transaction_out_id == candidate_out.id)
                .filter(TransferLink.transaction_in_id == candidate_in.id)
                .first()
            )
            assert link is not None
            self.assertEqual(link.status, "suggested")
            self.assertIn("auto_guard=generic_outflow", link.rationale)
            tx_out = db.query(Transaction).filter(Transaction.id == candidate_out.id).first()
            tx_in = db.query(Transaction).filter(Transaction.id == candidate_in.id).first()
            assert tx_out is not None
            assert tx_in is not None
            self.assertEqual(tx_out.meaning, "unknown")
            self.assertEqual(tx_in.meaning, "unknown")

    def test_lane_prior_keeps_ambiguous_same_amount_candidates_suggested(self) -> None:
        with self._Session() as db:
            acc_out, st_out = self._create_account_statement(
                db, provider="ozon", account_type="card", display_name="amb-out"
            )
            acc_in_a, st_in_a = self._create_account_statement(
                db, provider="sber", account_type="card", display_name="amb-in-a"
            )

            baseline = datetime(2026, 1, 10, 8, 0, 0)
            for idx in range(4):
                out_tx = self._create_statement_transaction(
                    db,
                    statement=st_out,
                    account_id=acc_out.id,
                    amount=2700.00,
                    direction="out",
                    operation_datetime=baseline + timedelta(days=idx),
                    description_raw="Перевод на копилка",
                    bank_category="transfer",
                )
                in_tx = self._create_statement_transaction(
                    db,
                    statement=st_in_a,
                    account_id=acc_in_a.id,
                    amount=2700.00,
                    direction="in",
                    operation_datetime=baseline + timedelta(days=idx, hours=1),
                    description_raw="Перевод с копилка",
                    bank_category="transfer",
                )
                self._add_confirmed_link(db, tx_out=out_tx, tx_in=in_tx)

            candidate_out = self._create_statement_transaction(
                db,
                statement=st_out,
                account_id=acc_out.id,
                amount=2900.00,
                direction="out",
                operation_datetime=datetime(2026, 2, 22, 10, 0, 0),
                description_raw="Перевод на копилка",
                bank_category="transfer",
            )
            candidate_in_a = self._create_statement_transaction(
                db,
                statement=st_in_a,
                account_id=acc_in_a.id,
                amount=2900.00,
                direction="in",
                operation_datetime=datetime(2026, 2, 22, 10, 5, 0),
                description_raw="Перевод с копилка",
                bank_category="transfer",
            )
            candidate_in_b = self._create_statement_transaction(
                db,
                statement=st_in_a,
                account_id=acc_in_a.id,
                amount=2900.00,
                direction="in",
                operation_datetime=datetime(2026, 2, 22, 10, 5, 0),
                description_raw="Перевод с копилка",
                bank_category="transfer",
            )
            db.commit()

            result = detect_transfer_links_in_session(db)
            db.commit()

            self.assertEqual(result.auto_links_created, 0)
            self.assertEqual(result.suggested_links_created, 1)
            links = (
                db.query(TransferLink)
                .filter(TransferLink.transaction_out_id == candidate_out.id)
                .all()
            )
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0].status, "suggested")
            self.assertIn("ambiguous=1", links[0].rationale)
            self.assertIn(
                links[0].transaction_in_id,
                {candidate_in_a.id, candidate_in_b.id},
            )


if __name__ == "__main__":
    unittest.main()
