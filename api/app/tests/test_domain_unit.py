from __future__ import annotations

import unittest
from typing import Any, ClassVar
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.domain.net_worth import build_balance_snapshots, compute_net_worth_timeline
from app.domain.transactions import dedupe, fingerprint, normalize_row
from app.domain.transfers import (
    ScoredTransferPair,
    TransferTx,
    score_transfer_pair,
    select_transfer_links,
)
from app.services.statement_parser import ParsedStatementBundle
from app.tests.fixture_data import (
    build_account_scopes,
    build_parsed_bundles,
    build_statement_balance_inputs,
    load_crossbank_dataset,
)


class DomainUnitTests(unittest.TestCase):
    dataset: ClassVar[dict[str, Any]]
    bundles: ClassVar[list[ParsedStatementBundle]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_crossbank_dataset()
        cls.bundles = build_parsed_bundles(cls.dataset)

    def _row_and_tx(self, statement_id: str, row_index: int):
        for bundle in self.bundles:
            for row in bundle.rows:
                if row.row_index != row_index:
                    continue
                if (row.raw_data or {}).get("fixture_statement_id") != statement_id:
                    continue
                tx = next((t for idx, t in bundle.txs if idx == row_index), None)
                if tx:
                    return row, tx
        raise AssertionError(f"Missing row fixture {statement_id}:{row_index}")

    def test_fingerprint_is_deterministic_and_time_sensitive(self) -> None:
        row, tx = self._row_and_tx("st_ozon_main", 3)
        candidate_a = normalize_row(
            row=row, tx=tx, account_id="acc_ozon", statement_row_id="row-main"
        )
        candidate_b = normalize_row(
            row=row, tx=tx, account_id="acc_ozon", statement_row_id="row-other"
        )

        self.assertEqual(fingerprint(candidate_a), fingerprint(candidate_b))

        row_late, tx_late = self._row_and_tx("st_ozon_main", 4)
        candidate_late = normalize_row(
            row=row_late,
            tx=tx_late,
            account_id="acc_ozon",
            statement_row_id="row-late",
        )
        self.assertNotEqual(fingerprint(candidate_a), fingerprint(candidate_late))

    def test_normalize_row_threads_timestamp_precision_and_source_ordering(self) -> None:
        row = SimpleNamespace(
            operation_date=datetime(2026, 1, 1, 0, 0, 0),
            posting_date=None,
            amount=Decimal("123.45"),
            currency="RUB",
            direction="out",
            raw_text="01.01.2026 Example",
            page_number=4,
            row_index=27,
            timestamp_precision="date_only",
        )
        tx = SimpleNamespace(
            operation_datetime=datetime(2026, 1, 1, 12, 34, 0),
            posting_datetime=None,
            amount=Decimal("123.45"),
            currency="RUB",
            direction="out",
            description_raw="Example",
            merchant_normalized="",
            bank_reference_id="",
            bank_category="",
            meaning="unknown",
            meaning_confidence=None,
            category="",
            tags=None,
            timestamp_precision="inferred",
        )

        candidate = normalize_row(
            row=row,
            tx=tx,
            account_id="acc-test",
            statement_row_id="row-1",
            statement_id="stmt-1",
        )

        self.assertEqual(candidate.timestamp_precision, "inferred")
        self.assertEqual(candidate.source_statement_id, "stmt-1")
        self.assertEqual(candidate.source_page_number, 4)
        self.assertEqual(candidate.source_row_index, 27)

    def test_fingerprint_distinguishes_same_second_sber_inflows_by_counterparty_reference(
        self,
    ) -> None:
        operation_dt = datetime.fromisoformat("2025-02-26T17:48:00")
        posting_dt = datetime.fromisoformat("2025-02-26T00:00:00")

        row_ivan = SimpleNamespace(
            operation_date=operation_dt,
            posting_date=posting_dt,
            amount=Decimal("26.00"),
            currency="RUB",
            direction="in",
            raw_text=(
                "26.02.2025 17:48 Перевод СБП +26,00 "
                "26.02.2025 SBP689480 Перевод от С. Иван Сергеевич. "
                "Операция по счету ****1002"
            ),
        )
        tx_ivan = SimpleNamespace(
            operation_datetime=operation_dt,
            posting_datetime=posting_dt,
            amount=Decimal("26.00"),
            currency="RUB",
            direction="in",
            description_raw=(
                "17:48 Перевод СБП +26,00 Перевод от С. Иван Сергеевич. Операция по счету ****1002"
            ),
            merchant_normalized="",
            bank_reference_id="SBP689480",
            bank_category="transfer",
            meaning="unknown",
            meaning_confidence=None,
            category="",
            tags=None,
        )

        row_alexander = SimpleNamespace(
            operation_date=operation_dt,
            posting_date=posting_dt,
            amount=Decimal("26.00"),
            currency="RUB",
            direction="in",
            raw_text=(
                "26.02.2025 17:48 Перевод СБП +26,00 "
                "26.02.2025 SBP199523 Перевод от З. Александр Алексеевич. "
                "Операция по счету ****1002"
            ),
        )
        tx_alexander = SimpleNamespace(
            operation_datetime=operation_dt,
            posting_datetime=posting_dt,
            amount=Decimal("26.00"),
            currency="RUB",
            direction="in",
            description_raw=(
                "17:48 Перевод СБП +26,00 Перевод от З. Александр Алексеевич. "
                "Операция по счету ****1002"
            ),
            merchant_normalized="",
            bank_reference_id="SBP199523",
            bank_category="transfer",
            meaning="unknown",
            meaning_confidence=None,
            category="",
            tags=None,
        )

        candidate_ivan = normalize_row(
            row=row_ivan,
            tx=tx_ivan,
            account_id="acc_sber",
            statement_row_id="sber-row-ivan",
        )
        candidate_alexander = normalize_row(
            row=row_alexander,
            tx=tx_alexander,
            account_id="acc_sber",
            statement_row_id="sber-row-alexander",
        )

        self.assertNotEqual(fingerprint(candidate_ivan), fingerprint(candidate_alexander))

        dedupe_result = dedupe([candidate_ivan, candidate_alexander], existing={})
        self.assertEqual(len(dedupe_result.canonical_transactions), 2)
        self.assertEqual(len(dedupe_result.statement_row_links), 2)

    def test_dedupe_handles_overlap_rows_without_double_counting(self) -> None:
        row_main, tx_main = self._row_and_tx("st_ozon_main", 3)
        row_overlap, tx_overlap = self._row_and_tx("st_ozon_overlap", 0)

        candidate_main = normalize_row(
            row=row_main,
            tx=tx_main,
            account_id="acc_ozon",
            statement_row_id="row-main",
        )
        candidate_overlap = normalize_row(
            row=row_overlap,
            tx=tx_overlap,
            account_id="acc_ozon",
            statement_row_id="row-overlap",
        )

        result = dedupe([candidate_main, candidate_overlap], existing={})
        self.assertEqual(len(result.canonical_transactions), 1)
        self.assertEqual(len(result.statement_row_links), 2)

        existing_result = dedupe(
            [candidate_main, candidate_overlap],
            existing={fingerprint(candidate_main): "tx-existing"},
        )
        self.assertEqual(len(existing_result.canonical_transactions), 0)
        self.assertEqual(existing_result.deduped_existing_count, 2)

    def test_transfer_scoring_and_selection(self) -> None:
        tx_out_auto = TransferTx(
            id="auto-out",
            account_id="acc_ozon",
            direction="out",
            currency="RUB",
            amount_cents=500_000,
            timestamp=datetime.fromisoformat("2026-01-10T10:00:00"),
            description_raw="Перевод между своими счетами",
            bank_category="transfer",
        )
        tx_in_auto = TransferTx(
            id="auto-in",
            account_id="acc_sber",
            direction="in",
            currency="RUB",
            amount_cents=500_000,
            timestamp=datetime.fromisoformat("2026-01-10T10:03:00"),
            description_raw="Перевод между своими счетами",
            bank_category="transfer",
        )
        tx_out_suggest = TransferTx(
            id="suggest-out",
            account_id="acc_yandex",
            direction="out",
            currency="RUB",
            amount_cents=120_000,
            timestamp=datetime.fromisoformat("2026-01-11T12:00:00"),
            description_raw="Transfer to own card",
            bank_category="",
        )
        tx_in_suggest = TransferTx(
            id="suggest-in",
            account_id="acc_ozon",
            direction="in",
            currency="RUB",
            amount_cents=120_000,
            timestamp=datetime.fromisoformat("2026-01-11T12:10:00"),
            description_raw="Card replenishment",
            bank_category="",
        )
        tx_out_fee = TransferTx(
            id="fee-out",
            account_id="acc_sber",
            direction="out",
            currency="RUB",
            amount_cents=100_000,
            timestamp=datetime.fromisoformat("2026-01-13T10:00:00"),
            description_raw="Transfer between accounts",
            bank_category="transfer",
        )
        tx_in_fee = TransferTx(
            id="fee-in",
            account_id="acc_yandex",
            direction="in",
            currency="RUB",
            amount_cents=99_500,
            timestamp=datetime.fromisoformat("2026-01-13T10:02:00"),
            description_raw="Transfer between accounts",
            bank_category="transfer",
        )
        tx_out_merchant = TransferTx(
            id="merchant-out",
            account_id="acc_ozon",
            direction="out",
            currency="RUB",
            amount_cents=90_000,
            timestamp=datetime.fromisoformat("2026-01-12T09:40:00"),
            description_raw="Оплата СБП OOO MARKET",
            bank_category="shopping",
        )

        auto_pair = score_transfer_pair(tx_out_auto, tx_in_auto)
        suggest_pair = score_transfer_pair(tx_out_suggest, tx_in_suggest)
        fee_pair = score_transfer_pair(tx_out_fee, tx_in_fee)
        merchant_pair = score_transfer_pair(tx_out_merchant, tx_in_suggest)

        self.assertIsNotNone(auto_pair)
        self.assertIsNotNone(suggest_pair)
        self.assertIsNotNone(fee_pair)
        self.assertIsNone(merchant_pair)

        assert auto_pair is not None
        assert suggest_pair is not None
        assert fee_pair is not None

        self.assertGreaterEqual(auto_pair.score, 0.92)
        self.assertGreaterEqual(suggest_pair.score, 0.80)
        self.assertLess(suggest_pair.score, 0.92)
        self.assertAlmostEqual(fee_pair.fee_amount or 0.0, 5.0, places=2)

        selection = select_transfer_links([auto_pair, suggest_pair, fee_pair])
        auto_ids = {
            (item.transaction_out_id, item.transaction_in_id) for item in selection.auto_links
        }
        suggested_ids = {
            (item.transaction_out_id, item.transaction_in_id) for item in selection.suggested_links
        }

        self.assertIn(("auto-out", "auto-in"), auto_ids)
        self.assertIn(("fee-out", "fee-in"), auto_ids)
        self.assertIn(("suggest-out", "suggest-in"), suggested_ids)

    def test_transfer_selection_uses_deterministic_tiebreak_before_marking_ambiguous(self) -> None:
        t0 = datetime.fromisoformat("2026-01-20T10:00:00")
        tx_out = TransferTx(
            id="tie-out",
            account_id="acc-a",
            direction="out",
            currency="RUB",
            amount_cents=700_000,
            timestamp=t0,
            description_raw="Transfer between accounts",
            bank_category="transfer",
            bank_reference_id="REF-001",
        )
        tx_in_ref_match = TransferTx(
            id="tie-in-ref",
            account_id="acc-b",
            direction="in",
            currency="RUB",
            amount_cents=700_000,
            timestamp=t0 + timedelta(minutes=2),
            description_raw="Transfer between accounts",
            bank_category="transfer",
            bank_reference_id="REF-001",
        )
        tx_in_no_ref = TransferTx(
            id="tie-in-no-ref",
            account_id="acc-c",
            direction="in",
            currency="RUB",
            amount_cents=700_000,
            timestamp=t0 + timedelta(minutes=3),
            description_raw="Transfer between accounts",
            bank_category="transfer",
            bank_reference_id="",
        )

        pair_ref = score_transfer_pair(tx_out, tx_in_ref_match)
        pair_no_ref = score_transfer_pair(tx_out, tx_in_no_ref)
        self.assertIsNotNone(pair_ref)
        self.assertIsNotNone(pair_no_ref)
        assert pair_ref is not None
        assert pair_no_ref is not None

        selection = select_transfer_links([pair_no_ref, pair_ref])
        self.assertEqual(len(selection.auto_links), 1)
        self.assertEqual(len(selection.suggested_links), 0)
        self.assertEqual(selection.auto_links[0].transaction_out_id, "tie-out")
        self.assertEqual(selection.auto_links[0].transaction_in_id, "tie-in-ref")
        self.assertIn("ambiguous=0", selection.auto_links[0].rationale)
        self.assertIn("tiebreak_out=bank_ref", selection.auto_links[0].rationale)

        out_ambiguous = TransferTx(
            id="amb-out",
            account_id="acc-z",
            direction="out",
            currency="RUB",
            amount_cents=200_000,
            timestamp=t0,
            description_raw="Transfer between accounts",
            bank_category="transfer",
        )
        in_amb_a = TransferTx(
            id="amb-in-a",
            account_id="acc-y",
            direction="in",
            currency="RUB",
            amount_cents=200_000,
            timestamp=t0 + timedelta(minutes=2, seconds=10),
            description_raw="Transfer between accounts",
            bank_category="transfer",
        )
        in_amb_b = TransferTx(
            id="amb-in-b",
            account_id="acc-x",
            direction="in",
            currency="RUB",
            amount_cents=200_000,
            timestamp=t0 + timedelta(minutes=2, seconds=11),
            description_raw="Transfer between accounts",
            bank_category="transfer",
        )

        pair_amb_a = score_transfer_pair(out_ambiguous, in_amb_a)
        pair_amb_b = score_transfer_pair(out_ambiguous, in_amb_b)
        self.assertIsNotNone(pair_amb_a)
        self.assertIsNotNone(pair_amb_b)
        assert pair_amb_a is not None
        assert pair_amb_b is not None

        ambiguous_selection = select_transfer_links([pair_amb_a, pair_amb_b])
        self.assertEqual(len(ambiguous_selection.auto_links), 0)
        self.assertEqual(len(ambiguous_selection.suggested_links), 1)
        self.assertIn("ambiguous=1", ambiguous_selection.suggested_links[0].rationale)

    def test_generic_outflow_without_reference_requires_stronger_auto_evidence(self) -> None:
        t0 = datetime.fromisoformat("2026-01-22T18:00:00")

        tx_out_generic = TransferTx(
            id="generic-out",
            account_id="acc-sber-savings",
            direction="out",
            currency="RUB",
            amount_cents=600_000,
            timestamp=t0,
            description_raw="Списание",
            bank_category="",
        )
        tx_in_hinted = TransferTx(
            id="hinted-in",
            account_id="acc-spb",
            direction="in",
            currency="RUB",
            amount_cents=600_000,
            timestamp=t0 + timedelta(minutes=2),
            description_raw="Перевод по СБП",
            bank_category="transfer",
        )

        pair = score_transfer_pair(tx_out_generic, tx_in_hinted)
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertGreaterEqual(pair.score, 0.92)
        self.assertFalse(pair.out_hint)
        self.assertTrue(pair.in_hint)
        self.assertFalse(pair.bank_reference_match)
        self.assertTrue(pair.outflow_requires_stronger_evidence)

        selection = select_transfer_links([pair])
        self.assertEqual(len(selection.auto_links), 0)
        self.assertEqual(len(selection.suggested_links), 1)
        self.assertIn("auto_guard=generic_outflow", selection.suggested_links[0].rationale)

        tx_out_generic_ref = TransferTx(
            id="generic-out-ref",
            account_id="acc-sber-savings",
            direction="out",
            currency="RUB",
            amount_cents=600_000,
            timestamp=t0,
            description_raw="Списание",
            bank_category="",
            bank_reference_id="REF-6000",
        )
        tx_in_hinted_ref = TransferTx(
            id="hinted-in-ref",
            account_id="acc-spb",
            direction="in",
            currency="RUB",
            amount_cents=600_000,
            timestamp=t0 + timedelta(minutes=2),
            description_raw="Перевод по СБП",
            bank_category="transfer",
            bank_reference_id="REF-6000",
        )

        pair_ref = score_transfer_pair(tx_out_generic_ref, tx_in_hinted_ref)
        self.assertIsNotNone(pair_ref)
        assert pair_ref is not None
        self.assertTrue(pair_ref.bank_reference_match)

        selection_ref = select_transfer_links([pair_ref])
        self.assertEqual(len(selection_ref.auto_links), 1)
        self.assertEqual(len(selection_ref.suggested_links), 0)
        self.assertIn("auto_guard=none", selection_ref.auto_links[0].rationale)

    def test_transfer_selection_auto_ignores_sub_auto_ambiguous_competitor(self) -> None:
        pair_high = ScoredTransferPair(
            transaction_out_id="out-1",
            transaction_in_id="in-high",
            score=0.94,
            rationale="manual-high",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=90.0,
            signed_delta_seconds=90.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
        )
        pair_sub_auto = ScoredTransferPair(
            transaction_out_id="out-1",
            transaction_in_id="in-sub-auto",
            score=0.91,
            rationale="manual-sub-auto",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=100.0,
            signed_delta_seconds=100.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=True,
            outflow_requires_stronger_evidence=False,
        )

        selection = select_transfer_links([pair_sub_auto, pair_high])
        self.assertEqual(len(selection.auto_links), 1)
        self.assertEqual(len(selection.suggested_links), 0)
        self.assertEqual(selection.auto_links[0].transaction_out_id, "out-1")
        self.assertEqual(selection.auto_links[0].transaction_in_id, "in-high")
        self.assertIn("ambiguous=0", selection.auto_links[0].rationale)
        self.assertIn("tiebreak_out=bank_ref", selection.auto_links[0].rationale)

    def test_transfer_selection_keeps_ambiguous_when_both_competitors_are_auto_eligible(
        self,
    ) -> None:
        pair_high = ScoredTransferPair(
            transaction_out_id="out-2",
            transaction_in_id="in-high",
            score=0.95,
            rationale="manual-high",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=90.0,
            signed_delta_seconds=90.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
        )
        pair_runner_up = ScoredTransferPair(
            transaction_out_id="out-2",
            transaction_in_id="in-runner-up",
            score=0.93,
            rationale="manual-runner-up",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=100.0,
            signed_delta_seconds=100.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=True,
            outflow_requires_stronger_evidence=False,
        )

        selection = select_transfer_links([pair_runner_up, pair_high])
        self.assertEqual(len(selection.auto_links), 0)
        self.assertEqual(len(selection.suggested_links), 1)
        self.assertEqual(selection.suggested_links[0].transaction_out_id, "out-2")
        self.assertEqual(selection.suggested_links[0].transaction_in_id, "in-high")
        self.assertIn("ambiguous=1", selection.suggested_links[0].rationale)
        self.assertIn("tiebreak_out=bank_ref", selection.suggested_links[0].rationale)

    def test_transfer_selection_prefers_counterparty_overlap_before_marking_ambiguous(self) -> None:
        t0 = datetime.fromisoformat("2026-01-23T08:00:00")
        tx_out = TransferTx(
            id="cp-out",
            account_id="acc-a",
            direction="out",
            currency="RUB",
            amount_cents=450_000,
            timestamp=t0,
            description_raw="Перевод для ИВАНОВ",
            bank_category="transfer",
        )
        tx_in_counterparty_match = TransferTx(
            id="cp-in-match",
            account_id="acc-b",
            direction="in",
            currency="RUB",
            amount_cents=450_000,
            timestamp=t0 + timedelta(minutes=2),
            description_raw="Перевод от ИВАНОВ",
            bank_category="transfer",
        )
        tx_in_counterparty_miss = TransferTx(
            id="cp-in-miss",
            account_id="acc-c",
            direction="in",
            currency="RUB",
            amount_cents=450_000,
            timestamp=t0 + timedelta(minutes=2, seconds=10),
            description_raw="Перевод от ПЕТРОВ",
            bank_category="transfer",
        )

        pair_match = score_transfer_pair(tx_out, tx_in_counterparty_match)
        pair_miss = score_transfer_pair(tx_out, tx_in_counterparty_miss)
        self.assertIsNotNone(pair_match)
        self.assertIsNotNone(pair_miss)
        assert pair_match is not None
        assert pair_miss is not None
        self.assertGreater(
            pair_match.counterparty_overlap_count, pair_miss.counterparty_overlap_count
        )

        selection = select_transfer_links([pair_miss, pair_match])
        self.assertEqual(len(selection.auto_links), 1)
        self.assertEqual(len(selection.suggested_links), 0)
        self.assertEqual(selection.auto_links[0].transaction_out_id, "cp-out")
        self.assertEqual(selection.auto_links[0].transaction_in_id, "cp-in-match")
        self.assertIn("ambiguous=0", selection.auto_links[0].rationale)
        self.assertIn("tiebreak_out=counterparty", selection.auto_links[0].rationale)

    def test_transfer_selection_prefers_account_pair_chronology_lane(self) -> None:
        pair_main_lane = ScoredTransferPair(
            transaction_out_id="lane-out-main",
            transaction_in_id="lane-in-main",
            score=0.94,
            rationale="manual-main-lane",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=90.0,
            signed_delta_seconds=90.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-a",
            in_account_id="acc-b",
        )
        pair_main_alt = ScoredTransferPair(
            transaction_out_id="lane-out-main",
            transaction_in_id="lane-in-alt",
            score=0.94,
            rationale="manual-main-alt",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=90.0,
            signed_delta_seconds=90.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-a",
            in_account_id="acc-c",
        )
        pair_history_1 = ScoredTransferPair(
            transaction_out_id="lane-out-h1",
            transaction_in_id="lane-in-h1",
            score=0.93,
            rationale="manual-h1",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=100.0,
            signed_delta_seconds=100.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-a",
            in_account_id="acc-b",
        )
        pair_history_2 = ScoredTransferPair(
            transaction_out_id="lane-out-h2",
            transaction_in_id="lane-in-h2",
            score=0.93,
            rationale="manual-h2",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=110.0,
            signed_delta_seconds=110.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-a",
            in_account_id="acc-b",
        )

        selection = select_transfer_links(
            [pair_main_alt, pair_history_1, pair_history_2, pair_main_lane]
        )
        self.assertEqual(len(selection.auto_links), 3)
        self.assertEqual(len(selection.suggested_links), 0)

        main_link = next(
            link for link in selection.auto_links if link.transaction_out_id == "lane-out-main"
        )
        self.assertEqual(main_link.transaction_in_id, "lane-in-main")
        self.assertIn("ambiguous=0", main_link.rationale)
        self.assertIn("tiebreak_out=account_pair_chronology", main_link.rationale)

    def test_transfer_selection_auto_resolves_two_by_two_by_total_score(self) -> None:
        pair_o1_i1 = ScoredTransferPair(
            transaction_out_id="o1",
            transaction_in_id="i1",
            score=0.99,
            rationale="manual-o1-i1",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=10.0,
            signed_delta_seconds=10.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-out",
            in_account_id="acc-in-1",
            counterparty_overlap_count=1,
        )
        pair_o1_i2 = ScoredTransferPair(
            transaction_out_id="o1",
            transaction_in_id="i2",
            score=0.97,
            rationale="manual-o1-i2",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=11.0,
            signed_delta_seconds=11.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-out",
            in_account_id="acc-in-2",
            counterparty_overlap_count=2,
        )
        pair_o2_i1 = ScoredTransferPair(
            transaction_out_id="o2",
            transaction_in_id="i1",
            score=0.97,
            rationale="manual-o2-i1",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=11.0,
            signed_delta_seconds=11.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-out",
            in_account_id="acc-in-1",
            counterparty_overlap_count=2,
        )
        pair_o2_i2 = ScoredTransferPair(
            transaction_out_id="o2",
            transaction_in_id="i2",
            score=0.99,
            rationale="manual-o2-i2",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=10.0,
            signed_delta_seconds=10.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-out",
            in_account_id="acc-in-2",
            counterparty_overlap_count=1,
        )

        selection = select_transfer_links([pair_o1_i2, pair_o2_i1, pair_o1_i1, pair_o2_i2])
        self.assertEqual(len(selection.auto_links), 2)
        self.assertEqual(len(selection.suggested_links), 0)
        auto_pairs = {
            (link.transaction_out_id, link.transaction_in_id) for link in selection.auto_links
        }
        self.assertEqual(auto_pairs, {("o1", "i1"), ("o2", "i2")})
        self.assertTrue(
            any("auto_override=2x2_total_score" in link.rationale for link in selection.auto_links)
        )

    def test_transfer_selection_auto_resolves_symmetric_two_by_two_deterministically(self) -> None:
        pair_o1_i1 = ScoredTransferPair(
            transaction_out_id="o1",
            transaction_in_id="i1",
            score=0.99,
            rationale="manual-o1-i1",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=5.0,
            signed_delta_seconds=5.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-out",
            in_account_id="acc-in",
            counterparty_overlap_count=2,
        )
        pair_o1_i2 = ScoredTransferPair(
            transaction_out_id="o1",
            transaction_in_id="i2",
            score=0.99,
            rationale="manual-o1-i2",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=5.0,
            signed_delta_seconds=5.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-out",
            in_account_id="acc-in",
            counterparty_overlap_count=2,
        )
        pair_o2_i1 = ScoredTransferPair(
            transaction_out_id="o2",
            transaction_in_id="i1",
            score=0.99,
            rationale="manual-o2-i1",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=5.0,
            signed_delta_seconds=5.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-out",
            in_account_id="acc-in",
            counterparty_overlap_count=2,
        )
        pair_o2_i2 = ScoredTransferPair(
            transaction_out_id="o2",
            transaction_in_id="i2",
            score=0.99,
            rationale="manual-o2-i2",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=5.0,
            signed_delta_seconds=5.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-out",
            in_account_id="acc-in",
            counterparty_overlap_count=2,
        )

        selection = select_transfer_links([pair_o1_i1, pair_o1_i2, pair_o2_i1, pair_o2_i2])
        self.assertEqual(len(selection.auto_links), 2)
        self.assertEqual(len(selection.suggested_links), 0)
        used_out = {link.transaction_out_id for link in selection.auto_links}
        used_in = {link.transaction_in_id for link in selection.auto_links}
        self.assertEqual(used_out, {"o1", "o2"})
        self.assertEqual(used_in, {"i1", "i2"})
        self.assertTrue(
            any(
                "auto_override=2x2_deterministic_symmetry" in link.rationale
                for link in selection.auto_links
            )
        )

    def test_transfer_selection_uses_component_global_optimum(self) -> None:
        pair_o1_i1 = ScoredTransferPair(
            transaction_out_id="g-o1",
            transaction_in_id="g-i1",
            score=0.95,
            rationale="manual-g-o1-i1",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=20.0,
            signed_delta_seconds=20.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-g-out-1",
            in_account_id="acc-g-in-1",
        )
        pair_o1_i2 = ScoredTransferPair(
            transaction_out_id="g-o1",
            transaction_in_id="g-i2",
            score=0.94,
            rationale="manual-g-o1-i2",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=20.0,
            signed_delta_seconds=20.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-g-out-1",
            in_account_id="acc-g-in-2",
        )
        pair_o2_i1 = ScoredTransferPair(
            transaction_out_id="g-o2",
            transaction_in_id="g-i1",
            score=0.94,
            rationale="manual-g-o2-i1",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=20.0,
            signed_delta_seconds=20.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-g-out-2",
            in_account_id="acc-g-in-1",
        )
        pair_o2_i2 = ScoredTransferPair(
            transaction_out_id="g-o2",
            transaction_in_id="g-i2",
            score=0.81,
            rationale="manual-g-o2-i2",
            fee_amount=None,
            amount_delta_cents=0,
            time_delta_seconds=20.0,
            signed_delta_seconds=20.0,
            out_hint=True,
            in_hint=True,
            bank_reference_match=False,
            outflow_requires_stronger_evidence=False,
            out_account_id="acc-g-out-2",
            in_account_id="acc-g-in-2",
        )

        selection = select_transfer_links([pair_o1_i1, pair_o1_i2, pair_o2_i1, pair_o2_i2])
        selected_ids = {
            (item.transaction_out_id, item.transaction_in_id)
            for item in [*selection.auto_links, *selection.suggested_links]
        }
        self.assertEqual(selected_ids, {("g-o1", "g-i2"), ("g-o2", "g-i1")})
        self.assertTrue(
            all("component_mode=exact" in item.rationale for item in selection.suggested_links)
        )

    def test_transfer_selection_uses_deterministic_fallback_for_large_components(self) -> None:
        pairs: list[ScoredTransferPair] = []
        for idx in range(11):
            pairs.append(
                ScoredTransferPair(
                    transaction_out_id=f"fb-o-{idx}",
                    transaction_in_id=f"fb-i-{idx}",
                    score=0.90,
                    rationale=f"manual-fb-main-{idx}",
                    fee_amount=None,
                    amount_delta_cents=0,
                    time_delta_seconds=60.0,
                    signed_delta_seconds=60.0,
                    out_hint=True,
                    in_hint=True,
                    bank_reference_match=False,
                    outflow_requires_stronger_evidence=False,
                    out_account_id=f"acc-fb-out-{idx}",
                    in_account_id=f"acc-fb-in-{idx}",
                )
            )
            if idx < 10:
                pairs.append(
                    ScoredTransferPair(
                        transaction_out_id=f"fb-o-{idx}",
                        transaction_in_id=f"fb-i-{idx + 1}",
                        score=0.89,
                        rationale=f"manual-fb-cross-{idx}",
                        fee_amount=None,
                        amount_delta_cents=0,
                        time_delta_seconds=70.0,
                        signed_delta_seconds=70.0,
                        out_hint=True,
                        in_hint=True,
                        bank_reference_match=False,
                        outflow_requires_stronger_evidence=False,
                        out_account_id=f"acc-fb-out-{idx}",
                        in_account_id=f"acc-fb-in-{idx + 1}",
                    )
                )

        first = select_transfer_links(pairs)
        second = select_transfer_links(pairs)
        first_ids = {
            (item.transaction_out_id, item.transaction_in_id)
            for item in [*first.auto_links, *first.suggested_links]
        }
        second_ids = {
            (item.transaction_out_id, item.transaction_in_id)
            for item in [*second.auto_links, *second.suggested_links]
        }
        self.assertEqual(first_ids, second_ids)
        self.assertTrue(
            any("component_mode=fallback" in item.rationale for item in first.suggested_links)
        )

    def test_transfer_selection_auto_promotes_recurrent_lane_pairs(self) -> None:
        pairs: list[ScoredTransferPair] = []
        for idx in range(15):
            pairs.append(
                ScoredTransferPair(
                    transaction_out_id=f"rec-out-{idx}",
                    transaction_in_id=f"rec-in-{idx}",
                    score=0.84,
                    rationale=f"manual-rec-{idx}",
                    fee_amount=None,
                    amount_delta_cents=0,
                    time_delta_seconds=4 * 60 * 60,
                    signed_delta_seconds=4 * 60 * 60,
                    out_hint=True,
                    in_hint=True,
                    bank_reference_match=False,
                    outflow_requires_stronger_evidence=False,
                    out_account_id="acc-y-card",
                    in_account_id="acc-y-savings",
                    counterparty_overlap_count=1,
                )
            )

        selection = select_transfer_links(pairs)
        self.assertEqual(len(selection.auto_links), 15)
        self.assertEqual(len(selection.suggested_links), 0)
        self.assertTrue(
            all("auto_lane=recurrent" in link.rationale for link in selection.auto_links)
        )

    def test_transfer_selection_keeps_non_recurrent_lane_pairs_suggested(self) -> None:
        pairs: list[ScoredTransferPair] = []
        for idx in range(5):
            pairs.append(
                ScoredTransferPair(
                    transaction_out_id=f"weak-out-{idx}",
                    transaction_in_id=f"weak-in-{idx}",
                    score=0.84,
                    rationale=f"manual-weak-{idx}",
                    fee_amount=None,
                    amount_delta_cents=0,
                    time_delta_seconds=4 * 60 * 60,
                    signed_delta_seconds=4 * 60 * 60,
                    out_hint=True,
                    in_hint=True,
                    bank_reference_match=False,
                    outflow_requires_stronger_evidence=False,
                    out_account_id="acc-y-card",
                    in_account_id="acc-y-savings",
                    counterparty_overlap_count=1,
                )
            )

        selection = select_transfer_links(pairs)
        self.assertEqual(len(selection.auto_links), 0)
        self.assertEqual(len(selection.suggested_links), 5)
        self.assertTrue(
            all("auto_lane=none" in link.rationale for link in selection.suggested_links)
        )

    def test_transfer_selection_auto_promotes_recurrent_lane_with_reverse_timestamps(self) -> None:
        pairs: list[ScoredTransferPair] = []
        for idx in range(15):
            pairs.append(
                ScoredTransferPair(
                    transaction_out_id=f"rev-out-{idx}",
                    transaction_in_id=f"rev-in-{idx}",
                    score=0.84,
                    rationale=f"manual-rev-{idx}",
                    fee_amount=None,
                    amount_delta_cents=0,
                    time_delta_seconds=8 * 60 * 60,
                    signed_delta_seconds=-(8 * 60 * 60),
                    out_hint=True,
                    in_hint=True,
                    bank_reference_match=False,
                    outflow_requires_stronger_evidence=False,
                    out_account_id="acc-y-card",
                    in_account_id="acc-y-savings",
                    counterparty_overlap_count=2,
                )
            )

        selection = select_transfer_links(pairs)
        self.assertEqual(len(selection.auto_links), 15)
        self.assertEqual(len(selection.suggested_links), 0)
        self.assertTrue(
            all("auto_lane=recurrent" in link.rationale for link in selection.auto_links)
        )

    def test_transfer_scoring_sets_known_internal_lane_marker_flags(self) -> None:
        tx_out = TransferTx(
            id="out-lane-marker",
            account_id="acc-savings",
            direction="out",
            currency="RUB",
            amount_cents=100_000,
            timestamp=datetime.fromisoformat("2025-03-01T00:00:00+00:00"),
            description_raw="Списание",
            bank_category="transfer",
            bank_reference_id="",
        )
        tx_in = TransferTx(
            id="in-lane-marker",
            account_id="acc-card",
            direction="in",
            currency="RUB",
            amount_cents=100_000,
            timestamp=datetime.fromisoformat("2025-03-01T10:30:00+00:00"),
            description_raw=(
                "10:30 Прочие операции +1 000,00 "
                "SBERBANK ONL@IN VKLAD-KARTA. Операция по карте ****1001"
            ),
            bank_category="",
            bank_reference_id="",
        )

        pair = score_transfer_pair(tx_out, tx_in, window=timedelta(days=2))
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertFalse(pair.out_known_internal_lane_marker)
        self.assertTrue(pair.in_known_internal_lane_marker)
        self.assertIn("known_lane_marker=0/1", pair.rationale)

    def test_transfer_selection_auto_promotes_one_sided_known_lane_pairs(self) -> None:
        pairs: list[ScoredTransferPair] = []
        for idx in range(50):
            pairs.append(
                ScoredTransferPair(
                    transaction_out_id=f"one-side-out-{idx}",
                    transaction_in_id=f"one-side-in-{idx}",
                    score=0.74,
                    rationale=f"manual-one-side-{idx}",
                    fee_amount=None,
                    amount_delta_cents=0,
                    time_delta_seconds=10 * 60 * 60,
                    signed_delta_seconds=10 * 60 * 60,
                    out_hint=True,
                    in_hint=False,
                    bank_reference_match=False,
                    outflow_requires_stronger_evidence=False,
                    out_account_id="acc-savings",
                    in_account_id="acc-card",
                    counterparty_overlap_count=0,
                    out_known_internal_lane_marker=False,
                    in_known_internal_lane_marker=True,
                )
            )

        selection = select_transfer_links(pairs)
        self.assertEqual(len(selection.auto_links), 50)
        self.assertEqual(len(selection.suggested_links), 0)
        self.assertTrue(
            all("auto_lane=one_sided_recurrent" in link.rationale for link in selection.auto_links)
        )

    def test_transfer_selection_auto_promotes_one_sided_known_lane_at_threshold(self) -> None:
        pairs: list[ScoredTransferPair] = []
        for idx in range(45):
            pairs.append(
                ScoredTransferPair(
                    transaction_out_id=f"one-side-th-out-{idx}",
                    transaction_in_id=f"one-side-th-in-{idx}",
                    score=0.74,
                    rationale=f"manual-one-side-th-{idx}",
                    fee_amount=None,
                    amount_delta_cents=0,
                    time_delta_seconds=10 * 60 * 60,
                    signed_delta_seconds=10 * 60 * 60,
                    out_hint=True,
                    in_hint=False,
                    bank_reference_match=False,
                    outflow_requires_stronger_evidence=False,
                    out_account_id="acc-savings",
                    in_account_id="acc-card",
                    counterparty_overlap_count=0,
                    out_known_internal_lane_marker=False,
                    in_known_internal_lane_marker=True,
                )
            )

        selection = select_transfer_links(pairs)
        self.assertEqual(len(selection.auto_links), 45)
        self.assertEqual(len(selection.suggested_links), 0)
        self.assertTrue(
            all("auto_lane=one_sided_recurrent" in link.rationale for link in selection.auto_links)
        )

    def test_transfer_selection_skips_one_sided_lane_without_known_marker(self) -> None:
        pairs: list[ScoredTransferPair] = []
        for idx in range(50):
            pairs.append(
                ScoredTransferPair(
                    transaction_out_id=f"one-side-weak-out-{idx}",
                    transaction_in_id=f"one-side-weak-in-{idx}",
                    score=0.74,
                    rationale=f"manual-one-side-weak-{idx}",
                    fee_amount=None,
                    amount_delta_cents=0,
                    time_delta_seconds=10 * 60 * 60,
                    signed_delta_seconds=10 * 60 * 60,
                    out_hint=True,
                    in_hint=False,
                    bank_reference_match=False,
                    outflow_requires_stronger_evidence=False,
                    out_account_id="acc-savings",
                    in_account_id="acc-card",
                    counterparty_overlap_count=0,
                    out_known_internal_lane_marker=False,
                    in_known_internal_lane_marker=False,
                )
            )

        selection = select_transfer_links(pairs)
        self.assertEqual(len(selection.auto_links), 0)
        self.assertEqual(len(selection.suggested_links), 0)

    def test_transfer_selection_does_not_auto_promote_one_sided_known_lane_below_threshold(
        self,
    ) -> None:
        pairs: list[ScoredTransferPair] = []
        for idx in range(44):
            pairs.append(
                ScoredTransferPair(
                    transaction_out_id=f"one-side-below-out-{idx}",
                    transaction_in_id=f"one-side-below-in-{idx}",
                    score=0.74,
                    rationale=f"manual-one-side-below-{idx}",
                    fee_amount=None,
                    amount_delta_cents=0,
                    time_delta_seconds=10 * 60 * 60,
                    signed_delta_seconds=10 * 60 * 60,
                    out_hint=True,
                    in_hint=False,
                    bank_reference_match=False,
                    outflow_requires_stronger_evidence=False,
                    out_account_id="acc-savings",
                    in_account_id="acc-card",
                    counterparty_overlap_count=0,
                    out_known_internal_lane_marker=False,
                    in_known_internal_lane_marker=True,
                )
            )

        selection = select_transfer_links(pairs)
        self.assertEqual(len(selection.auto_links), 0)
        self.assertEqual(len(selection.suggested_links), 0)

    def test_transfer_selection_does_not_suggest_low_score_one_sided_lane_candidates(self) -> None:
        pairs: list[ScoredTransferPair] = []
        for idx in range(10):
            pairs.append(
                ScoredTransferPair(
                    transaction_out_id=f"one-side-low-out-{idx}",
                    transaction_in_id=f"one-side-low-in-{idx}",
                    score=0.74,
                    rationale=f"manual-one-side-low-{idx}",
                    fee_amount=None,
                    amount_delta_cents=0,
                    time_delta_seconds=10 * 60 * 60,
                    signed_delta_seconds=10 * 60 * 60,
                    out_hint=True,
                    in_hint=False,
                    bank_reference_match=False,
                    outflow_requires_stronger_evidence=False,
                    out_account_id="acc-savings",
                    in_account_id="acc-card",
                    counterparty_overlap_count=0,
                    out_known_internal_lane_marker=False,
                    in_known_internal_lane_marker=True,
                )
            )

        selection = select_transfer_links(pairs)
        self.assertEqual(len(selection.auto_links), 0)
        self.assertEqual(len(selection.suggested_links), 0)

    def test_net_worth_timeline_completeness(self) -> None:
        statement_inputs = build_statement_balance_inputs(self.dataset)
        scopes = build_account_scopes(self.dataset)

        snapshots = build_balance_snapshots(statement_inputs)
        timeline = compute_net_worth_timeline(
            snapshots=snapshots,
            accounts=scopes,
            granularity="raw",
        )

        self.assertEqual(timeline["granularity"], "raw")
        self.assertEqual(len(timeline["series"]), 1)
        points = timeline["series"][0]["points"]
        self.assertGreater(len(points), 0)

        last_point = points[-1]
        # Fixture dataset current expected sum of latest balances across all included accounts.
        self.assertAlmostEqual(last_point["total_balance"], 37922.0, places=2)
        self.assertEqual(last_point["accounts_total"], 3)
        self.assertEqual(last_point["accounts_with_snapshot"], 3)
        self.assertAlmostEqual(last_point["completeness"], 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
