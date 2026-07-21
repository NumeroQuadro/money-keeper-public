from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.domain.metrics_quality import build_metrics_quality_payload
from app.models import Statement, StatementRow, Transaction, TransferLink
from app.services.metrics_quality import build_metrics_quality_report
from app.tests.db_test_utils import get_test_engine


class MetricsQualityReportTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        with engine.begin() as conn:
            schema = conn.exec_driver_sql("select current_schema()::text").scalar_one()
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{schema}"."transaction"')
        Base.metadata.create_all(bind=engine)
        self._engine = engine
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def _seed_canonical_transactions(self, db) -> tuple[Transaction, Transaction]:
        tx_out = Transaction(
            amount=100,
            currency="RUB",
            direction="out",
            operation_datetime=datetime(2026, 1, 10, 10, 0, 0),
            description_raw="Transfer candidate out",
            meaning="unknown",
        )
        tx_in = Transaction(
            amount=100,
            currency="RUB",
            direction="in",
            operation_datetime=datetime(2026, 1, 10, 10, 1, 0),
            description_raw="Transfer candidate in",
            meaning="unknown",
        )
        tx_auto_out = Transaction(
            amount=50,
            currency="RUB",
            direction="out",
            operation_datetime=datetime(2026, 1, 11, 10, 0, 0),
            description_raw="Confirmed transfer out",
            meaning="internal_transfer",
        )
        tx_auto_in = Transaction(
            amount=50,
            currency="RUB",
            direction="in",
            operation_datetime=datetime(2026, 1, 11, 10, 2, 0),
            description_raw="Confirmed transfer in",
            meaning="internal_transfer",
        )
        db.add_all([tx_out, tx_in, tx_auto_out, tx_auto_in])
        db.flush()

        db.add_all(
            [
                TransferLink(
                    transaction_out_id=tx_out.id,
                    transaction_in_id=tx_in.id,
                    status="suggested",
                    rationale="candidate",
                ),
                TransferLink(
                    transaction_out_id=tx_auto_out.id,
                    transaction_in_id=tx_auto_in.id,
                    status="auto",
                    rationale="high confidence",
                ),
            ]
        )
        db.flush()
        return tx_out, tx_in

    def test_report_marks_warning_when_legacy_drift_and_suggestions_exist(self) -> None:
        with self._engine.begin() as conn:
            conn.exec_driver_sql(
                'CREATE TABLE "transaction" ('
                "id serial primary key, direction varchar not null, amount numeric(14,2) not null)"
            )
            conn.exec_driver_sql(
                'INSERT INTO "transaction" (direction, amount) VALUES '
                "('out', -90.00), ('in', 130.00)"
            )

        with self._Session() as db:
            self._seed_canonical_transactions(db)
            db.commit()

            report = build_metrics_quality_report(db)

        self.assertTrue(report["legacy_table_exists"])
        self.assertEqual(report["canonical_reporting_table"], "transactions")
        self.assertEqual(report["legacy_reporting_table"], "transaction")
        self.assertTrue(report["active_search_path"])
        self.assertEqual(report["legacy_parity"]["status"], "drift")
        self.assertIsNotNone(report["legacy_parity"]["transactions_total_delta_pct"])
        self.assertEqual(report["quality"]["status"], "warning")
        self.assertIn("legacy_canonical_drift", report["quality"]["flags"])
        self.assertIn("suggested_transfer_links_pending", report["quality"]["flags"])
        self.assertGreater(len(report["quality"]["recommendations"]), 0)
        self.assertEqual(report["suggested_links"], 1)
        self.assertAlmostEqual(report["unresolved_transfer_gross_impact"], 200.0, places=2)
        self.assertEqual(report["reconciliation_mismatch_statements"], 0)
        self.assertEqual(report["orphan_statement_link_rows"], 0)
        self.assertEqual(report["unlinked_statement_rows"], 0)
        self.assertEqual(report["unlinked_transactions"], 4)
        self.assertEqual(report["rls_disabled_public_tables"], 0)
        self.assertEqual(report["functions_without_explicit_search_path"], 0)

    def test_report_is_ok_without_quality_flags(self) -> None:
        with self._Session() as db:
            tx_out = Transaction(
                amount=15,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 5, 9, 0, 0),
                description_raw="Coffee",
                meaning="spend",
            )
            tx_in = Transaction(
                amount=25,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 6, 9, 0, 0),
                description_raw="Salary",
                meaning="income",
            )
            db.add_all([tx_out, tx_in])
            db.commit()

            report = build_metrics_quality_report(db)

        self.assertFalse(report["legacy_table_exists"])
        self.assertTrue(report["canonical_table_exists"])
        self.assertEqual(report["canonical_reporting_table"], "transactions")
        self.assertNotEqual(report["reporting_schema"], "")
        self.assertNotEqual(report["active_search_path"], "")
        self.assertIsNone(report["legacy_reporting_table"])
        self.assertEqual(report["legacy_parity"]["status"], "not_present")
        if report["reporting_schema"] == "public":
            self.assertEqual(report["quality"]["status"], "ok")
            self.assertEqual(report["quality"]["flags"], [])
            self.assertEqual(report["quality"]["recommendations"], [])
        else:
            self.assertEqual(report["quality"]["status"], "warning")
            self.assertEqual(report["quality"]["flags"], ["non_public_reporting_schema"])
            self.assertTrue(report["quality"]["recommendations"])
        self.assertEqual(report["reconciliation_mismatch_statements"], 0)
        self.assertEqual(report["orphan_statement_link_rows"], 0)
        self.assertEqual(report["unlinked_statement_rows"], 0)
        self.assertEqual(report["unlinked_transactions"], 2)
        self.assertEqual(report["rls_disabled_public_tables"], 0)
        self.assertEqual(report["functions_without_explicit_search_path"], 0)

    def test_report_marks_warning_when_reconciliation_mismatch_exists(self) -> None:
        with self._Session() as db:
            db.add(
                Statement(
                    provider="ozon",
                    account_display="Main account",
                    statement_type="card",
                    currency="RUB",
                    reconcile_status="mismatch",
                    pdf_path="/tmp/statement.pdf",
                )
            )
            db.commit()

            report = build_metrics_quality_report(db)

        self.assertEqual(report["quality"]["status"], "warning")
        self.assertIn("statement_reconciliation_mismatch", report["quality"]["flags"])
        self.assertEqual(report["reconciliation_mismatch_statements"], 1)
        self.assertEqual(report["orphan_statement_link_rows"], 0)

    def test_report_dedupes_unresolved_transfer_impact_per_transaction(self) -> None:
        with self._Session() as db:
            tx_out = Transaction(
                amount=100,
                currency="RUB",
                direction="out",
                operation_datetime=datetime(2026, 1, 12, 10, 0, 0),
                description_raw="Transfer out",
                meaning="unknown",
            )
            tx_in_1 = Transaction(
                amount=100,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 12, 10, 1, 0),
                description_raw="Transfer in 1",
                meaning="unknown",
            )
            tx_in_2 = Transaction(
                amount=100,
                currency="RUB",
                direction="in",
                operation_datetime=datetime(2026, 1, 12, 10, 2, 0),
                description_raw="Transfer in 2",
                meaning="unknown",
            )
            db.add_all([tx_out, tx_in_1, tx_in_2])
            db.flush()

            db.add_all(
                [
                    TransferLink(
                        transaction_out_id=tx_out.id,
                        transaction_in_id=tx_in_1.id,
                        status="suggested",
                        rationale="candidate 1",
                    ),
                    TransferLink(
                        transaction_out_id=tx_out.id,
                        transaction_in_id=tx_in_2.id,
                        status="suggested",
                        rationale="candidate 2",
                    ),
                ]
            )
            db.commit()

            report = build_metrics_quality_report(db)

        self.assertEqual(report["suggested_links"], 2)
        self.assertEqual(report["unique_tx_in_suggested_links"], 3)
        self.assertAlmostEqual(report["suggested_outflow_amount"], 100.0, places=2)
        self.assertEqual(report["unlinked_transactions"], 3)

    def test_report_marks_warning_for_unlinked_statement_rows(self) -> None:
        with self._Session() as db:
            statement = Statement(
                provider="ozon",
                account_display="Main account",
                statement_type="card",
                currency="RUB",
                reconcile_status="ok",
                pdf_path="/tmp/statement.pdf",
            )
            db.add(statement)
            db.flush()

            db.add(
                StatementRow(
                    statement_id=statement.id,
                    row_index=1,
                    page_number=1,
                    raw_text="Unlinked row",
                    currency="RUB",
                    direction="out",
                )
            )
            db.commit()

            report = build_metrics_quality_report(db)

        self.assertEqual(report["quality"]["status"], "warning")
        self.assertIn("statement_rows_unlinked", report["quality"]["flags"])
        self.assertEqual(report["unlinked_statement_rows"], 1)

    def test_payload_marks_critical_when_canonical_table_is_missing(self) -> None:
        def run_query(query: str):
            if "current_schema()::text as reporting_schema" in query:
                return {
                    "canonical_relation": None,
                    "legacy_relation": None,
                    "reporting_schema": "public",
                    "active_search_path": '"$user", public, extensions',
                }
            return {}

        def table_exists(_: str) -> bool:
            return False

        report = build_metrics_quality_payload(run_query=run_query, table_exists=table_exists)

        self.assertFalse(report["canonical_table_exists"])
        self.assertEqual(report["quality"]["status"], "critical")
        self.assertIn("canonical_reporting_table_missing", report["quality"]["flags"])
        self.assertGreater(report["transactions_total"], -1)

    def test_payload_marks_warning_for_security_exposure_flags(self) -> None:
        def run_query(query: str):
            if "current_schema()::text as reporting_schema" in query:
                return {
                    "canonical_relation": "transactions",
                    "legacy_relation": None,
                    "reporting_schema": "public",
                    "active_search_path": "public, extensions",
                }
            if "rls_disabled_public_tables" in query:
                return {
                    "rls_disabled_public_tables": 2,
                    "rls_disabled_public_table_samples": ["accounts", "transactions"],
                }
            if "functions_without_explicit_search_path" in query:
                return {
                    "functions_without_explicit_search_path": 1,
                    "functions_without_explicit_search_path_samples": [
                        "public.match_documents(vector,double precision,integer)"
                    ],
                }
            return {}

        def table_exists(name: str) -> bool:
            return name == "transactions"

        report = build_metrics_quality_payload(run_query=run_query, table_exists=table_exists)

        self.assertEqual(report["quality"]["status"], "warning")
        self.assertIn("public_tables_without_rls", report["quality"]["flags"])
        self.assertIn("functions_without_explicit_search_path", report["quality"]["flags"])
        self.assertEqual(report["rls_disabled_public_tables"], 2)
        self.assertEqual(report["functions_without_explicit_search_path"], 1)


if __name__ == "__main__":
    unittest.main()
