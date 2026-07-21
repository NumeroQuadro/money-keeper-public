from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_backfill_module():
    script_path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "backfill_sber_savings_description_evidence.py"
    )
    spec = importlib.util.spec_from_file_location(
        "backfill_sber_savings_description_evidence_script", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/backfill_sber_savings_description_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backfill_script = _load_backfill_module()


class BackfillSberSavingsDescriptionEvidenceScriptTests(unittest.TestCase):
    def test_normalize_statement_raw_text_for_description(self) -> None:
        text = (
            "16.09.2024 Зачисление к/с 40817 810 1 0000 0000002 02, "
            "№ 100000000001-52 +1 000,00 103 984,00 ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ"
        )
        normalized = backfill_script._normalize_statement_raw_text_for_description(text)
        self.assertTrue(normalized.startswith("Зачисление к/с 40817"))
        self.assertIn("№ 100000000001-52", normalized)
        self.assertNotIn("16.09.2024", normalized)
        self.assertNotIn("ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ", normalized)

    def test_plan_updates_prefers_richest_statement_row_text(self) -> None:
        rows = [
            {
                "transaction_id": "tx-1",
                "source_file": "all_saving_sberbank_1002.pdf",
                "description_raw": "Зачисление",
                "statement_row_id": "sr-1",
                "statement_row_raw_text": "16.09.2024 Зачисление",
            },
            {
                "transaction_id": "tx-1",
                "source_file": "all_saving_sberbank_1002.pdf",
                "description_raw": "Зачисление",
                "statement_row_id": "sr-2",
                "statement_row_raw_text": (
                    "16.09.2024 Зачисление к/с 40817 810 1 0000 0000002 02, "
                    "№ 100000000001-52 +1 000,00 103 984,00"
                ),
            },
        ]

        plan = backfill_script._plan_updates(rows, sample_limit=10)
        self.assertEqual(plan["candidate_transactions"], 1)
        self.assertEqual(plan["planned_updates"], 1)
        self.assertEqual(len(plan["operations"]), 1)

        operation = plan["operations"][0]
        self.assertEqual(operation["before_description_raw"], "Зачисление")
        self.assertIn("к/с", operation["after_description_raw"])
        self.assertIn("№ 100000000001-52", operation["after_description_raw"])

    def test_plan_updates_skips_rows_when_description_is_already_enriched(self) -> None:
        rows = [
            {
                "transaction_id": "tx-2",
                "source_file": "all_saving_sberbank_1003.pdf",
                "description_raw": (
                    "Списание к/с 40817 810 6 0000 0000003 03, "
                    "№ 100000000001-184 -1 400,00 149 575,73"
                ),
                "statement_row_id": "sr-10",
                "statement_row_raw_text": (
                    "09.09.2025 Списание к/с 40817 810 6 0000 0000003 03, "
                    "№ 100000000001-184 -1 400,00 149 575,73"
                ),
            }
        ]

        plan = backfill_script._plan_updates(rows, sample_limit=10)
        self.assertEqual(plan["candidate_transactions"], 1)
        self.assertEqual(plan["planned_updates"], 0)
        self.assertEqual(plan["operations"], [])


if __name__ == "__main__":
    unittest.main()
