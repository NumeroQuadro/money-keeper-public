from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.domain.transactions import TxCandidate, fingerprint as canonical_fingerprint


def _load_backfill_module():
    script_path = (
        Path(__file__).resolve().parents[3] / "scripts" / "backfill_same_statement_collapses.py"
    )
    spec = importlib.util.spec_from_file_location(
        "backfill_same_statement_collapses_script", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/backfill_same_statement_collapses.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backfill_script = _load_backfill_module()


class BackfillSameStatementCollapsesScriptTests(unittest.TestCase):
    def _collapse_row(self) -> dict:
        return {
            "transaction_id": "tx-1",
            "account_id": "acc-1",
            "dedup_key": "k-original",
            "transaction_amount": Decimal("15000.00"),
            "transaction_currency": "RUB",
            "transaction_direction": "in",
            "operation_datetime": datetime(2024, 1, 5, 9, 0, 0),
            "posting_datetime": datetime(2024, 1, 5, 9, 5, 0),
            "transaction_bank_reference_id": "",
            "transaction_bank_category": "transfer",
            "transaction_description_raw": "Зачисление зарплаты",
            "supporting_rows": 2,
            "distinct_statements": 1,
            "distinct_source_files": 1,
            "supporting_rows_detail": [
                {
                    "statement_row_id": "sr-1",
                    "statement_id": "st-1",
                    "source_file": "all_card_spb.pdf",
                    "row_index": 10,
                    "page_number": 1,
                    "row_direction": "in",
                    "row_amount": "15000.00",
                    "row_currency": "RUB",
                    "operation_date": datetime(2024, 1, 5, 9, 0, 0),
                    "posting_date": datetime(2024, 1, 5, 9, 5, 0),
                    "row_bank_reference_id": "",
                    "row_bank_category": "transfer",
                    "row_raw_text": "05.01.2024 09:00 Зачисление зарплаты",
                },
                {
                    "statement_row_id": "sr-2",
                    "statement_id": "st-1",
                    "source_file": "all_card_spb.pdf",
                    "row_index": 11,
                    "page_number": 1,
                    "row_direction": "in",
                    "row_amount": "15000.00",
                    "row_currency": "RUB",
                    "operation_date": datetime(2024, 1, 5, 9, 0, 0),
                    "posting_date": datetime(2024, 1, 5, 9, 5, 0),
                    "row_bank_reference_id": "",
                    "row_bank_category": "transfer",
                    "row_raw_text": "05.01.2024 09:00 Зачисление зарплаты",
                },
            ],
        }

    def test_collect_actionable_collapses_plans_deterministic_split(self) -> None:
        rows = [self._collapse_row()]
        first_collapses, first_diag = backfill_script._collect_actionable_collapses(rows)
        second_collapses, second_diag = backfill_script._collect_actionable_collapses(rows)

        self.assertEqual(first_diag["collapsed_row_surplus"], 1)
        self.assertEqual(first_diag["actionable_transaction_count"], 1)
        self.assertEqual(first_diag["planned_split_rows"], 1)
        self.assertEqual(first_diag, second_diag)
        self.assertEqual(first_collapses, second_collapses)

        collapse = first_collapses[0]
        self.assertEqual(collapse["keep_statement_row_id"], "sr-1")
        self.assertEqual(len(collapse["operations"]), 1)
        operation = collapse["operations"][0]
        self.assertEqual(operation["statement_row_id"], "sr-2")
        self.assertEqual(len(operation["synthetic_bank_reference_id"]), 24)
        self.assertEqual(len(operation["dedup_key"]), 64)

    def test_collect_actionable_collapses_blocks_rows_with_existing_references(self) -> None:
        row = self._collapse_row()
        row["transaction_bank_reference_id"] = "DOC-12345"
        collapses, diagnostics = backfill_script._collect_actionable_collapses([row])

        self.assertEqual(len(collapses), 0)
        self.assertEqual(diagnostics["blocked_by_reference_rows"], 1)
        self.assertEqual(diagnostics["planned_split_rows"], 0)

    def test_transaction_fingerprint_matches_canonical_algorithm(self) -> None:
        synthetic_ref = "abcdef0123456789abcdef01"
        amount = Decimal("321.45")
        operation_datetime = datetime(2025, 2, 1, 12, 30, 0)
        posting_datetime = datetime(2025, 2, 1, 13, 0, 0)
        description = "Перевод СБП тест"
        bank_category = "transfer"
        raw_text = "01.02.2025 12:30 Перевод СБП тест"

        from_script = backfill_script._transaction_fingerprint(
            account_id="acc-x",
            currency="RUB",
            direction="out",
            amount=amount,
            operation_datetime=operation_datetime,
            posting_datetime=posting_datetime,
            bank_reference_id=synthetic_ref,
            description_raw=description,
            bank_category=bank_category,
            raw_text=raw_text,
        )

        candidate = TxCandidate(
            account_id="acc-x",
            statement_row_id="sr-x",
            operation_datetime=operation_datetime,
            posting_datetime=posting_datetime,
            amount=amount,
            currency="RUB",
            direction="out",
            description_raw=description,
            merchant_normalized="",
            bank_reference_id=synthetic_ref,
            bank_category=bank_category,
            meaning="unknown",
            meaning_confidence=None,
            category="",
            tags=None,
            review_status="needs_review",
            raw_text=raw_text,
        )
        expected = canonical_fingerprint(candidate)
        self.assertEqual(from_script, expected)


if __name__ == "__main__":
    unittest.main()
