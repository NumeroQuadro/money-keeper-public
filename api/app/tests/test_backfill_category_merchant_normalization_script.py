from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_script_module():
    script_path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "backfill_category_merchant_normalization.py"
    )
    spec = importlib.util.spec_from_file_location("backfill_category_merchant_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/backfill_category_merchant_normalization.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script_module()


class BackfillCategoryMerchantScriptTests(unittest.TestCase):
    def test_directional_guardrail_recategorizes_non_reviewed_rows(self) -> None:
        self.assertEqual(
            script._apply_directional_category_guardrail(
                direction="out",
                category="Income",
                meaning="unknown",
                review_status="needs_review",
            ),
            "Spending",
        )
        self.assertEqual(
            script._apply_directional_category_guardrail(
                direction="in",
                category="Spending",
                meaning="unknown",
                review_status="needs_review",
            ),
            "Income",
        )

    def test_directional_guardrail_skips_reviewed_and_internal_transfer(self) -> None:
        self.assertEqual(
            script._apply_directional_category_guardrail(
                direction="out",
                category="Income",
                meaning="internal_transfer",
                review_status="needs_review",
            ),
            "Income",
        )
        self.assertEqual(
            script._apply_directional_category_guardrail(
                direction="out",
                category="Income",
                meaning="unknown",
                review_status="reviewed",
            ),
            "Income",
        )

    def test_derive_merchant_uses_existing_value(self) -> None:
        self.assertEqual(
            script._derive_merchant_normalized(
                merchant_normalized="Coffee House",
                description_raw="Оплата в Пятерочка",
                bank_category="",
                direction="out",
            ),
            "Coffee House",
        )

    def test_derive_merchant_extracts_inflow_sender(self) -> None:
        self.assertEqual(
            script._derive_merchant_normalized(
                merchant_normalized="",
                description_raw=(
                    "Перевод B52131631561680B0000120011570301 через СБП. "
                    "Отправитель: Иван Иванович И. Без НДС."
                ),
                bank_category="",
                direction="in",
            ),
            "Иван Иванович И",
        )

    def test_plan_updates_counts_category_and_merchant_changes(self) -> None:
        rows = [
            {
                "transaction_id": "tx-1",
                "direction": "out",
                "category": "Income",
                "meaning": "unknown",
                "review_status": "needs_review",
                "description_raw": "Оплата в Пятерочка",
                "bank_category": "",
                "merchant_normalized": "",
            },
            {
                "transaction_id": "tx-2",
                "direction": "in",
                "category": "Spending",
                "meaning": "unknown",
                "review_status": "needs_review",
                "description_raw": (
                    "Перевод B52131631561680B0000120011570301 через СБП. "
                    "Отправитель: Иван Иванович И. Без НДС."
                ),
                "bank_category": "",
                "merchant_normalized": "",
            },
            {
                "transaction_id": "tx-3",
                "direction": "out",
                "category": "Income",
                "meaning": "unknown",
                "review_status": "reviewed",
                "description_raw": "Reviewed explicit override",
                "bank_category": "",
                "merchant_normalized": "",
            },
        ]

        plan = script._plan_updates(rows, sample_limit=10)
        self.assertEqual(plan["scanned_transactions"], 3)
        self.assertEqual(plan["planned_updates"], 3)
        self.assertEqual(plan["category_updates"], 2)
        self.assertEqual(plan["merchant_updates"], 3)
        self.assertEqual(plan["both_updates"], 2)

        tx1 = next(item for item in plan["operations"] if item["transaction_id"] == "tx-1")
        self.assertEqual(tx1["after_category"], "Spending")
        self.assertNotEqual(tx1["after_merchant_normalized"], "")

        tx2 = next(item for item in plan["operations"] if item["transaction_id"] == "tx-2")
        self.assertEqual(tx2["after_category"], "Income")
        self.assertEqual(tx2["after_merchant_normalized"], "Иван Иванович И")

        tx3 = next(item for item in plan["operations"] if item["transaction_id"] == "tx-3")
        self.assertEqual(tx3["after_category"], "Income")
        self.assertTrue(tx3["merchant_changed"])


if __name__ == "__main__":
    unittest.main()
