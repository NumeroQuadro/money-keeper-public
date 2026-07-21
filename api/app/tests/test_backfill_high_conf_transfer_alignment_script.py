from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_script_module():
    script_path = (
        Path(__file__).resolve().parents[3] / "scripts" / "backfill_high_conf_transfer_alignment.py"
    )
    spec = importlib.util.spec_from_file_location(
        "backfill_high_conf_transfer_alignment", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/backfill_high_conf_transfer_alignment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script_module()


class BackfillHighConfidenceTransferAlignmentScriptTests(unittest.TestCase):
    def test_plan_demotes_internal_with_reference_clear_when_phrase_stays_high_conf(self) -> None:
        canonical_rows = [
            {
                "transaction_id": "tx-1",
                "source_file": "all_card_ozon.pdf",
                "operation_datetime": "2026-01-01T10:00:00",
                "page_number": 1,
                "direction": "out",
                "amount": "1000.00",
                "meaning": "internal_transfer",
                "bank_category": "transfer",
                "bank_reference_id": "ref-1",
                "description_raw": "Перевод для Дмитрия",
            }
        ]
        grouped = script._group_canonical_rows_by_key(
            canonical_rows, scope_regex=r"all_[^/]+\.pdf$"
        )
        plan = script._plan_alignment_operations(
            canonical_rows_by_key=grouped,
            reference_high_conf_counts={},
            promote_meaning_confidence=0.74,
        )

        self.assertEqual(plan["operation_count"], 1)
        self.assertEqual(
            plan["operations"][0]["action"],
            script.ACTION_DEMOTE_INTERNAL_CLEAR_REFERENCE,
        )
        self.assertEqual(plan["unresolved_count"], 0)

    def test_plan_demotes_unknown_high_conf_by_clearing_bank_reference(self) -> None:
        canonical_rows = [
            {
                "transaction_id": "tx-2",
                "source_file": "all_card_ozon.pdf",
                "operation_datetime": "2026-01-01T10:01:00",
                "page_number": 1,
                "direction": "out",
                "amount": "1200.00",
                "meaning": "unknown",
                "bank_category": "transfer",
                "bank_reference_id": "ref-2",
                "description_raw": "Перевод для Натальи",
            }
        ]
        grouped = script._group_canonical_rows_by_key(
            canonical_rows, scope_regex=r"all_[^/]+\.pdf$"
        )
        plan = script._plan_alignment_operations(
            canonical_rows_by_key=grouped,
            reference_high_conf_counts={},
            promote_meaning_confidence=0.74,
        )

        self.assertEqual(plan["operation_count"], 1)
        self.assertEqual(
            plan["operations"][0]["action"],
            script.ACTION_DEMOTE_UNKNOWN_CLEAR_REFERENCE,
        )

    def test_plan_promotes_unknown_to_internal_when_reference_high_conf_required(self) -> None:
        canonical_rows = [
            {
                "transaction_id": "tx-3",
                "source_file": "all_card_ozon.pdf",
                "operation_datetime": "2026-01-01T10:02:00",
                "page_number": 1,
                "direction": "in",
                "amount": "500.00",
                "meaning": "unknown",
                "bank_category": "transfer",
                "bank_reference_id": "",
                "description_raw": "Зачисление от Ивана",
            }
        ]
        grouped = script._group_canonical_rows_by_key(
            canonical_rows, scope_regex=r"all_[^/]+\.pdf$"
        )
        key = next(iter(grouped.keys()))
        plan = script._plan_alignment_operations(
            canonical_rows_by_key=grouped,
            reference_high_conf_counts={key: 1},
            promote_meaning_confidence=0.74,
        )

        self.assertEqual(plan["operation_count"], 1)
        self.assertEqual(plan["operations"][0]["action"], script.ACTION_PROMOTE_UNKNOWN)
        self.assertEqual(plan["operations"][0]["promote_meaning_confidence"], 0.74)


if __name__ == "__main__":
    unittest.main()
