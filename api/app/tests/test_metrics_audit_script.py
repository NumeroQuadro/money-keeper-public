from __future__ import annotations

import importlib.util
import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


def _load_metrics_audit_module():
    script_path = Path(__file__).resolve().parents[3] / "scripts" / "metrics_audit.py"
    spec = importlib.util.spec_from_file_location("metrics_audit_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load scripts/metrics_audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metrics_audit = _load_metrics_audit_module()


class MetricsAuditScriptValidationTests(unittest.TestCase):
    def test_validate_database_url_rejects_sqlite(self) -> None:
        with self.assertRaisesRegex(ValueError, "SQLite is not allowed"):
            metrics_audit._validate_database_url("sqlite:///tmp/money.db")

    def test_validate_database_url_rejects_local_host_outside_tests(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TESTING": "",
                "TEST_ALLOW_LOCAL_DB": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "Local database hosts are not allowed"):
                metrics_audit._validate_database_url("postgresql://user:pass@localhost:5432/db")

    def test_validate_database_url_allows_local_host_in_explicit_test_mode(self) -> None:
        with patch.dict(
            os.environ,
            {"TESTING": "1", "TEST_ALLOW_LOCAL_DB": "1"},
            clear=False,
        ):
            metrics_audit._validate_database_url("postgresql://user:pass@localhost:5432/db")

    def test_validate_database_search_path(self) -> None:
        self.assertEqual(
            metrics_audit._validate_database_search_path("public, analytics"),
            "public, analytics",
        )
        with self.assertRaisesRegex(ValueError, "comma-separated list of SQL identifiers"):
            metrics_audit._validate_database_search_path("public, bad-schema")

    def test_quality_summary_marks_warning_for_suggested_links(self) -> None:
        report = {
            "suggested_links": 2,
            "orphan_link_rows": 0,
            "legacy_parity": {"status": "ok"},
            "suggested_outflow_amount": "120.50",
            "suggested_inflow_amount": "100.00",
        }
        metrics_audit._attach_quality_summary(report)
        self.assertEqual(report["quality"]["status"], "warning")
        self.assertIn("suggested_transfer_links_pending", report["quality"]["flags"])
        self.assertGreater(len(report["quality"]["recommendations"]), 0)
        self.assertEqual(str(report["unresolved_transfer_net_impact"]), "-20.50")

    def test_quality_summary_marks_critical_for_orphan_links(self) -> None:
        report = {
            "suggested_links": 0,
            "orphan_link_rows": 1,
            "legacy_parity": {"status": "ok"},
            "suggested_outflow_amount": "0",
            "suggested_inflow_amount": "0",
        }
        metrics_audit._attach_quality_summary(report)
        self.assertEqual(report["quality"]["status"], "critical")
        self.assertIn("orphan_transfer_links", report["quality"]["flags"])

    def test_quality_summary_marks_warning_for_reconciliation_mismatch(self) -> None:
        report = {
            "suggested_links": 0,
            "orphan_link_rows": 0,
            "reconciliation_mismatch_statements": 2,
            "legacy_parity": {"status": "ok"},
            "suggested_outflow_amount": "0",
            "suggested_inflow_amount": "0",
        }
        metrics_audit._attach_quality_summary(report)
        self.assertEqual(report["quality"]["status"], "warning")
        self.assertIn("statement_reconciliation_mismatch", report["quality"]["flags"])

    def test_quality_summary_marks_critical_for_orphan_statement_links(self) -> None:
        report = {
            "suggested_links": 0,
            "orphan_link_rows": 0,
            "orphan_statement_link_rows": 2,
            "legacy_parity": {"status": "ok"},
            "suggested_outflow_amount": "0",
            "suggested_inflow_amount": "0",
        }
        metrics_audit._attach_quality_summary(report)
        self.assertEqual(report["quality"]["status"], "critical")
        self.assertIn("orphan_statement_links", report["quality"]["flags"])

    def test_quality_summary_marks_critical_when_canonical_table_missing(self) -> None:
        report = {
            "canonical_table_exists": False,
            "reporting_schema": "public",
            "suggested_links": 0,
            "orphan_link_rows": 0,
            "legacy_parity": {"status": "not_present"},
            "suggested_outflow_amount": "0",
            "suggested_inflow_amount": "0",
        }
        metrics_audit._attach_quality_summary(report)
        self.assertEqual(report["quality"]["status"], "critical")
        self.assertIn("canonical_reporting_table_missing", report["quality"]["flags"])

    def test_quality_summary_marks_warning_for_non_public_reporting_schema(self) -> None:
        report = {
            "canonical_table_exists": True,
            "reporting_schema": "test_schema",
            "suggested_links": 0,
            "orphan_link_rows": 0,
            "legacy_parity": {"status": "ok"},
            "suggested_outflow_amount": "0",
            "suggested_inflow_amount": "0",
        }
        metrics_audit._attach_quality_summary(report)
        self.assertEqual(report["quality"]["status"], "warning")
        self.assertIn("non_public_reporting_schema", report["quality"]["flags"])

    def test_quality_summary_marks_warning_for_public_tables_without_rls(self) -> None:
        report = {
            "canonical_table_exists": True,
            "reporting_schema": "public",
            "suggested_links": 0,
            "orphan_link_rows": 0,
            "rls_disabled_public_tables": 3,
            "legacy_parity": {"status": "ok"},
            "suggested_outflow_amount": "0",
            "suggested_inflow_amount": "0",
        }
        metrics_audit._attach_quality_summary(report)
        self.assertEqual(report["quality"]["status"], "warning")
        self.assertIn("public_tables_without_rls", report["quality"]["flags"])

    def test_quality_summary_marks_warning_for_functions_without_explicit_search_path(self) -> None:
        report = {
            "canonical_table_exists": True,
            "reporting_schema": "public",
            "suggested_links": 0,
            "orphan_link_rows": 0,
            "functions_without_explicit_search_path": 2,
            "legacy_parity": {"status": "ok"},
            "suggested_outflow_amount": "0",
            "suggested_inflow_amount": "0",
        }
        metrics_audit._attach_quality_summary(report)
        self.assertEqual(report["quality"]["status"], "warning")
        self.assertIn("functions_without_explicit_search_path", report["quality"]["flags"])


class MetricsAuditReferenceReconciliationTests(unittest.TestCase):
    def test_normalize_source_file_key_extracts_all_pdf_suffix(self) -> None:
        self.assertEqual(
            metrics_audit._normalize_source_file_key("20260210_1234abcd_all_card_ozon.pdf"),
            "all_card_ozon.pdf",
        )
        self.assertEqual(
            metrics_audit._normalize_source_file_key(
                "/tmp/uploads/20260210_hash_all_saving_sberbank_1003.pdf"
            ),
            "all_saving_sberbank_1003.pdf",
        )
        self.assertEqual(
            metrics_audit._normalize_source_file_key("all_card_spb.pdf"),
            "all_card_spb.pdf",
        )
        self.assertEqual(
            metrics_audit._normalize_source_file_key("month_card_ozon.pdf"),
            "month_card_ozon.pdf",
        )
        self.assertEqual(metrics_audit._normalize_source_file_key(None), "")

    def test_build_reference_metrics_from_rows_computes_tiers(self) -> None:
        rows = [
            {
                "operation_date": "2026-01-01",
                "amount_rub": "100.00",
                "is_transfer": "False",
                "transfer_confidence": "",
                "source_file": "all_card_ozon.pdf",
            },
            {
                "operation_date": "2026-01-02",
                "amount_rub": "-40.00",
                "is_transfer": "False",
                "transfer_confidence": "",
                "source_file": "all_card_ozon.pdf",
            },
            {
                "operation_date": "2026-01-03",
                "amount_rub": "50.00",
                "is_transfer": "True",
                "transfer_confidence": "high",
                "source_file": "all_card_sberbank_1004.pdf",
            },
            {
                "operation_date": "2026-01-03",
                "amount_rub": "-50.00",
                "is_transfer": "True",
                "transfer_confidence": "high",
                "source_file": "all_card_sberbank_1004.pdf",
            },
            {
                "operation_date": "2026-01-04",
                "amount_rub": "10.00",
                "is_transfer": "true",
                "transfer_confidence": "medium",
                "source_file": "all_card_spb.pdf",
            },
        ]
        metrics = metrics_audit._build_reference_metrics_from_rows(rows)

        gross = metrics["gross"]
        self.assertEqual(gross["tx_count"], 5)
        self.assertEqual(gross["inflow"], Decimal("160.00"))
        self.assertEqual(gross["outflow"], Decimal("90.00"))
        self.assertEqual(gross["net"], Decimal("70.00"))

        tier_b = metrics["tier_b_excluding_high_confidence_transfers"]
        self.assertEqual(tier_b["tx_count"], 3)
        self.assertEqual(tier_b["inflow"], Decimal("110.00"))
        self.assertEqual(tier_b["outflow"], Decimal("40.00"))
        self.assertEqual(tier_b["net"], Decimal("70.00"))

        tier_c = metrics["tier_c_excluding_all_transfers"]
        self.assertEqual(tier_c["tx_count"], 2)
        self.assertEqual(tier_c["income"], Decimal("100.00"))
        self.assertEqual(tier_c["spend"], Decimal("40.00"))
        self.assertEqual(tier_c["net"], Decimal("60.00"))

        per_file = metrics["per_file_gross"]
        self.assertIn("all_card_ozon.pdf", per_file)
        self.assertEqual(per_file["all_card_ozon.pdf"]["tx_count"], 2)
        self.assertEqual(per_file["all_card_ozon.pdf"]["net"], Decimal("60.00"))
        self.assertEqual(
            per_file["all_card_sberbank_1004.pdf"]["tx_count"],
            2,
        )

        per_file_tier_b = metrics["per_file_tier_b_excluding_high_confidence_transfers"]
        self.assertEqual(per_file_tier_b["all_card_ozon.pdf"]["tx_count"], 2)
        self.assertEqual(per_file_tier_b["all_card_spb.pdf"]["tx_count"], 1)
        self.assertNotIn("all_card_sberbank_1004.pdf", per_file_tier_b)

        per_file_tier_c = metrics["per_file_tier_c_excluding_all_transfers"]
        self.assertEqual(per_file_tier_c["all_card_ozon.pdf"]["tx_count"], 2)
        self.assertNotIn("all_card_spb.pdf", per_file_tier_c)

    def test_find_per_file_mismatches_reports_delta(self) -> None:
        actual = {
            "all_card_ozon.pdf": {
                "tx_count": 2,
                "inflow": Decimal("100.00"),
                "outflow": Decimal("40.00"),
                "net": Decimal("60.00"),
            }
        }
        expected = {
            "all_card_ozon.pdf": {
                "tx_count": 2,
                "inflow": Decimal("100.00"),
                "outflow": Decimal("40.00"),
                "net": Decimal("60.00"),
            },
            "all_card_spb.pdf": {
                "tx_count": 1,
                "inflow": Decimal("10.00"),
                "outflow": Decimal("0.00"),
                "net": Decimal("10.00"),
            },
        }
        mismatches = metrics_audit._find_per_file_mismatches(actual, expected)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["source_file"], "all_card_spb.pdf")
        self.assertEqual(mismatches[0]["delta"]["tx_count"], -1)
        self.assertEqual(mismatches[0]["delta"]["inflow"], Decimal("-10.00"))

    def test_per_file_mismatch_uses_normalized_source_file_key(self) -> None:
        actual = {
            metrics_audit._normalize_source_file_key("20260210_abcd_all_card_ozon.pdf"): {
                "tx_count": 2,
                "inflow": Decimal("100.00"),
                "outflow": Decimal("40.00"),
                "net": Decimal("60.00"),
            }
        }
        expected = {
            "all_card_ozon.pdf": {
                "tx_count": 2,
                "inflow": Decimal("100.00"),
                "outflow": Decimal("40.00"),
                "net": Decimal("60.00"),
            }
        }
        mismatches = metrics_audit._find_per_file_mismatches(actual, expected)
        self.assertEqual(mismatches, [])

    def test_statement_row_fingerprint_payload_normalizes_whitespace_and_case(self) -> None:
        row_a = {
            "row_currency": "rub",
            "row_direction": "out",
            "row_amount": Decimal("123.40"),
            "operation_date": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            "posting_date": None,
            "row_bank_reference_id": " REF-01 ",
            "row_bank_category": "Transfer",
            "row_raw_text": "  Перевод   себе  ",
        }
        row_b = {
            "row_currency": "RUB",
            "row_direction": "OUT",
            "row_amount": Decimal("123.4"),
            "operation_date": "2026-01-01T10:00:00+00:00",
            "posting_date": "",
            "row_bank_reference_id": "ref-01",
            "row_bank_category": " transfer ",
            "row_raw_text": "перевод себе",
        }
        payload_a = metrics_audit._statement_row_fingerprint_payload(account_id="acc-1", row=row_a)
        payload_b = metrics_audit._statement_row_fingerprint_payload(account_id="acc-1", row=row_b)
        self.assertEqual(payload_a, payload_b)

    def test_classify_collapse_type(self) -> None:
        self.assertEqual(
            metrics_audit._classify_collapse_type(distinct_statements=2, unique_row_fingerprints=1),
            "expected_overlap_dedupe",
        )
        self.assertEqual(
            metrics_audit._classify_collapse_type(distinct_statements=2, unique_row_fingerprints=2),
            "cross_statement_variance_merge",
        )
        self.assertEqual(
            metrics_audit._classify_collapse_type(distinct_statements=1, unique_row_fingerprints=1),
            "same_statement_duplicate_collapse",
        )
        self.assertEqual(
            metrics_audit._classify_collapse_type(distinct_statements=1, unique_row_fingerprints=2),
            "same_statement_variance_merge",
        )

    def test_summarize_canonical_collapse_rows_counts_expected_vs_unexpected(self) -> None:
        rows = [
            {
                "transaction_id": "tx-overlap",
                "dedup_key": "k-overlap",
                "account_id": "acc-1",
                "transaction_amount": Decimal("100.00"),
                "transaction_currency": "RUB",
                "transaction_direction": "out",
                "operation_datetime": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
                "posting_datetime": None,
                "transaction_bank_reference_id": "",
                "transaction_bank_category": "transfer",
                "transaction_description_raw": "перевод",
                "supporting_rows": 2,
                "distinct_statements": 2,
                "distinct_source_files": 1,
                "supporting_rows_detail": [
                    {
                        "statement_row_id": "sr-1",
                        "statement_id": "st-1",
                        "source_file": "all_card_ozon.pdf",
                        "row_index": 1,
                        "page_number": 1,
                        "row_direction": "out",
                        "row_amount": Decimal("100.00"),
                        "row_currency": "RUB",
                        "operation_date": "2026-01-01T10:00:00+00:00",
                        "posting_date": None,
                        "row_bank_reference_id": "",
                        "row_bank_category": "transfer",
                        "row_raw_text": "Перевод себе",
                    },
                    {
                        "statement_row_id": "sr-2",
                        "statement_id": "st-2",
                        "source_file": "all_card_ozon.pdf",
                        "row_index": 9,
                        "page_number": 2,
                        "row_direction": "out",
                        "row_amount": Decimal("100.00"),
                        "row_currency": "RUB",
                        "operation_date": "2026-01-01T10:00:00+00:00",
                        "posting_date": None,
                        "row_bank_reference_id": "",
                        "row_bank_category": "transfer",
                        "row_raw_text": "Перевод себе",
                    },
                ],
            },
            {
                "transaction_id": "tx-same-statement",
                "dedup_key": "k-same",
                "account_id": "acc-2",
                "transaction_amount": Decimal("500.00"),
                "transaction_currency": "RUB",
                "transaction_direction": "in",
                "operation_datetime": datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
                "posting_datetime": None,
                "transaction_bank_reference_id": "",
                "transaction_bank_category": "",
                "transaction_description_raw": "зачисление",
                "supporting_rows": 2,
                "distinct_statements": 1,
                "distinct_source_files": 1,
                "supporting_rows_detail": [
                    {
                        "statement_row_id": "sr-3",
                        "statement_id": "st-3",
                        "source_file": "all_card_spb.pdf",
                        "row_index": 4,
                        "page_number": 1,
                        "row_direction": "in",
                        "row_amount": Decimal("500.00"),
                        "row_currency": "RUB",
                        "operation_date": "2026-01-02T10:00:00+00:00",
                        "posting_date": None,
                        "row_bank_reference_id": "",
                        "row_bank_category": "",
                        "row_raw_text": "Зачисление от Ивана",
                    },
                    {
                        "statement_row_id": "sr-4",
                        "statement_id": "st-3",
                        "source_file": "all_card_spb.pdf",
                        "row_index": 5,
                        "page_number": 1,
                        "row_direction": "in",
                        "row_amount": Decimal("500.00"),
                        "row_currency": "RUB",
                        "operation_date": "2026-01-02T10:00:00+00:00",
                        "posting_date": None,
                        "row_bank_reference_id": "",
                        "row_bank_category": "",
                        "row_raw_text": "Зачисление от Марии",
                    },
                ],
            },
        ]

        summary = metrics_audit._summarize_canonical_collapse_rows(rows, detail_limit=1)
        self.assertEqual(summary["collapsed_transaction_count"], 2)
        self.assertEqual(summary["collapsed_row_surplus"], 2)
        self.assertEqual(summary["expected_overlap_collapsed_rows"], 1)
        self.assertEqual(summary["potential_unintended_collapsed_rows"], 1)
        self.assertEqual(summary["status"], "warning")
        self.assertEqual(len(summary["sample_collapses"]), 1)
        self.assertIn(
            "expected_overlap_dedupe",
            summary["collapse_type_breakdown"],
        )
        self.assertIn(
            "same_statement_variance_merge",
            summary["collapse_type_breakdown"],
        )

    def test_build_analytics_source_recommendation_uses_statement_rows_when_surplus(self) -> None:
        recommendation = metrics_audit._build_analytics_source_recommendation(
            statement_rows={"tx_count": 2458},
            canonical={"tx_count": 2450},
            collapse_audit={
                "collapsed_row_surplus": 8,
                "potential_unintended_collapsed_rows": 0,
            },
        )
        self.assertEqual(recommendation["tier_a_gross_source"], "statement_rows")
        self.assertEqual(recommendation["transfer_aware_source"], "canonical_transactions")
        self.assertEqual(recommendation["status"], "warning")
        self.assertGreaterEqual(len(recommendation["reasoning"]), 2)

    def test_build_high_confidence_classification_mismatch(self) -> None:
        reference_rows = [
            {
                "source_file": "all_card_ozon.pdf",
                "operation_datetime": "2026-01-01 10:00:00",
                "direction": "in",
                "amount_rub": "100.00",
                "page": "1",
                "is_transfer": "true",
                "transfer_confidence": "high",
                "description": "Перевод от Ивана",
            },
            {
                "source_file": "all_card_ozon.pdf",
                "operation_datetime": "2026-01-01 10:01:00",
                "direction": "out",
                "amount_rub": "-50.00",
                "page": "1",
                "is_transfer": "false",
                "transfer_confidence": "",
                "description": "Оплата",
            },
        ]
        canonical_rows = [
            {
                "source_file": "all_card_ozon.pdf",
                "operation_datetime": "2026-01-01T10:00:00+00:00",
                "direction": "in",
                "amount": "100.00",
                "page_number": 1,
                "meaning": "unknown",
                "bank_category": "transfer",
                "bank_reference_id": "ref-1",
                "description_raw": "Зачисление",
            },
            {
                "source_file": "all_card_ozon.pdf",
                "operation_datetime": "2026-01-01T10:01:00+00:00",
                "direction": "out",
                "amount": "50.00",
                "page_number": 1,
                "meaning": "unknown",
                "bank_category": "",
                "bank_reference_id": "",
                "description_raw": "Оплата",
            },
            {
                "source_file": "all_card_ozon.pdf",
                "operation_datetime": "2026-01-01T10:02:00+00:00",
                "direction": "in",
                "amount": "75.00",
                "page_number": 1,
                "meaning": "internal_transfer",
                "bank_category": "transfer",
                "bank_reference_id": "ref-2",
                "description_raw": "Перевод от Пети",
            },
        ]

        mismatch = metrics_audit._build_high_confidence_classification_mismatch(
            reference_rows=reference_rows,
            canonical_rows=canonical_rows,
            scope_regex=r"all_[^/]+\.pdf$",
            sample_limit=10,
        )

        self.assertEqual(mismatch["status"], "warning")
        self.assertEqual(mismatch["reference_row_count"], 2)
        self.assertEqual(mismatch["canonical_row_count"], 3)
        self.assertEqual(mismatch["row_count_delta"], 1)
        self.assertEqual(mismatch["reference_high_conf_row_count"], 1)
        self.assertEqual(mismatch["canonical_high_conf_row_count"], 1)
        self.assertEqual(mismatch["high_conf_row_delta"], 0)
        self.assertEqual(mismatch["key_count_mismatches"], 1)
        self.assertEqual(mismatch["high_conf_count_mismatches"], 2)
        self.assertEqual(len(mismatch["by_source_file"]), 1)
        self.assertEqual(mismatch["by_source_file"][0]["source_file"], "all_card_ozon.pdf")
        self.assertEqual(mismatch["by_source_file"][0]["mismatch_keys"], 2)
        self.assertEqual(len(mismatch["sample_mismatches"]), 2)
        self.assertTrue(mismatch["phrase_role_breakdown"])
        phrase_roles = {item["phrase_role"] for item in mismatch["phrase_role_breakdown"]}
        self.assertIn("sender_role", phrase_roles)
        self.assertIn("generic_credit", phrase_roles)

        self.assertTrue(mismatch["meaning_breakdown"])
        meanings = {item["meaning"] for item in mismatch["meaning_breakdown"]}
        self.assertIn("unknown", meanings)
        self.assertIn("internal_transfer", meanings)

        self.assertIn("phrase_role_tags", mismatch["sample_mismatches"][0])
        self.assertIn("canonical_primary_meaning", mismatch["sample_mismatches"][0])
        for role_bucket in mismatch["phrase_role_breakdown"]:
            self.assertIn("high_conf_inflow_amount_delta", role_bucket)
            self.assertIn("high_conf_outflow_amount_delta", role_bucket)
            self.assertIn("source_file_count", role_bucket)
        for meaning_bucket in mismatch["meaning_breakdown"]:
            self.assertIn("high_conf_inflow_amount_delta", meaning_bucket)
            self.assertIn("high_conf_outflow_amount_delta", meaning_bucket)
            self.assertIn("source_file_count", meaning_bucket)

    def test_high_confidence_phrase_role_tags(self) -> None:
        tags = metrics_audit._high_confidence_phrase_role_tags(
            direction="in",
            canonical_examples=("Зачисление",),
            reference_examples=("Перевод от Ивана",),
        )
        self.assertIn("sender_role", tags)
        self.assertIn("generic_credit", tags)

        protocol_tags = metrics_audit._high_confidence_phrase_role_tags(
            direction="out",
            canonical_examples=("Платеж через СБП",),
            reference_examples=(),
        )
        self.assertEqual(protocol_tags, ("sbp_protocol_only",))


if __name__ == "__main__":
    unittest.main()
