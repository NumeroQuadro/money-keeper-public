from __future__ import annotations

import unittest

from app.domain.cashflow_lens import (
    CASHFLOW_LENS_HIGH_CONFIDENCE_TRANSFER_LIKE,
    CASHFLOW_LENS_INTERNAL_ONLY,
    CASHFLOW_LENS_STRICT_TRANSFER_LIKE,
    DEFAULT_CASHFLOW_LENS,
    normalize_cashflow_lens,
    transfer_exclusion_params,
    transfer_exclusion_predicate_sql,
)


class CashflowLensTests(unittest.TestCase):
    def test_normalize_cashflow_lens_defaults_for_unknown_values(self) -> None:
        self.assertEqual(normalize_cashflow_lens(None), DEFAULT_CASHFLOW_LENS)
        self.assertEqual(normalize_cashflow_lens(""), DEFAULT_CASHFLOW_LENS)
        self.assertEqual(normalize_cashflow_lens("bogus"), DEFAULT_CASHFLOW_LENS)

    def test_transfer_exclusion_predicate_internal_only(self) -> None:
        predicate = transfer_exclusion_predicate_sql(
            alias="t", cashflow_lens=CASHFLOW_LENS_INTERNAL_ONLY
        )
        self.assertEqual(predicate, "coalesce(t.meaning, '') = :internal_transfer_meaning")

    def test_transfer_exclusion_predicate_strict_transfer_like(self) -> None:
        predicate = transfer_exclusion_predicate_sql(
            alias="t",
            cashflow_lens=CASHFLOW_LENS_STRICT_TRANSFER_LIKE,
        )
        self.assertIn("coalesce(t.meaning, '') = :internal_transfer_meaning", predicate)
        self.assertIn("coalesce(t.meaning, '') = :external_transfer_meaning", predicate)
        self.assertIn(
            "lower(coalesce(t.description_raw, '')) like :strict_transfer_account_flow_1",
            predicate,
        )
        self.assertIn(
            "lower(coalesce(t.description_raw, '')) like :strict_transfer_like_1",
            predicate,
        )
        self.assertIn(
            "lower(coalesce(t.description_raw, '')) like :strict_transfer_like_7",
            predicate,
        )
        self.assertIn("strict_transfer_exception_personal_transfer_1", predicate)
        self.assertIn("strict_transfer_exception_fee_2", predicate)
        self.assertNotIn("= :transfer_bank_category", predicate)

    def test_transfer_exclusion_predicate_high_confidence_transfer_like(self) -> None:
        predicate = transfer_exclusion_predicate_sql(
            alias="t",
            cashflow_lens=CASHFLOW_LENS_HIGH_CONFIDENCE_TRANSFER_LIKE,
        )
        self.assertIn("coalesce(t.meaning, '') = :internal_transfer_meaning", predicate)
        self.assertIn("coalesce(t.meaning, '') = :external_transfer_meaning", predicate)
        self.assertIn("lower(coalesce(t.bank_category, '')) = :transfer_bank_category", predicate)
        self.assertIn("coalesce(t.bank_reference_id, '') <> ''", predicate)
        self.assertIn("coalesce(t.direction, '') = 'in'", predicate)
        self.assertIn(
            "coalesce(t.amount, 0) >= :high_conf_transfer_min_in_amount",
            predicate,
        )
        self.assertIn(
            "coalesce(t.amount, 0) >= :high_conf_transfer_min_out_amount",
            predicate,
        )
        self.assertIn(
            "lower(coalesce(t.description_raw, '')) like :high_conf_transfer_like_in_1",
            predicate,
        )
        self.assertIn(
            "lower(coalesce(t.description_raw, '')) like :high_conf_transfer_like_in_4",
            predicate,
        )
        self.assertIn(
            "lower(coalesce(t.description_raw, '')) like :high_conf_transfer_like_out_1",
            predicate,
        )
        self.assertIn(
            "lower(coalesce(t.description_raw, '')) like :high_conf_transfer_like_out_3",
            predicate,
        )

    def test_transfer_exclusion_params_exposes_expected_markers(self) -> None:
        params = transfer_exclusion_params()
        self.assertEqual(params["internal_transfer_meaning"], "internal_transfer")
        self.assertEqual(params["external_transfer_meaning"], "external_transfer")
        self.assertEqual(params["transfer_bank_category"], "transfer")
        self.assertEqual(str(params["high_conf_transfer_min_in_amount"]), "185.00")
        self.assertEqual(str(params["high_conf_transfer_min_out_amount"]), "50.00")
        self.assertEqual(str(params["high_conf_transfer_min_amount"]), "100.00")
        self.assertEqual(params["high_conf_transfer_like_in_1"], "%перевод сбп%")
        self.assertEqual(params["high_conf_transfer_like_in_2"], "%через сбп%")
        self.assertEqual(params["high_conf_transfer_like_in_3"], "%зачисление к/с%")
        self.assertEqual(params["high_conf_transfer_like_in_4"], "%отправитель:%")
        self.assertEqual(params["high_conf_transfer_like_out_1"], "%перевод для%")
        self.assertEqual(params["high_conf_transfer_like_out_2"], "%перевод для%")
        self.assertEqual(params["high_conf_transfer_like_out_3"], "%перевод для%")
        self.assertEqual(params["strict_transfer_account_flow_1"], "%списание к/с%")
        self.assertEqual(params["strict_transfer_account_flow_2"], "%зачисление к/с%")
        self.assertEqual(params["strict_transfer_like_1"], "%перевод%")
        self.assertEqual(params["strict_transfer_like_7"], "%платеж сбп%")
        self.assertEqual(
            params["strict_transfer_exception_personal_transfer_1"],
            "%перевод денежных средств%",
        )
        self.assertEqual(
            params["strict_transfer_exception_personal_transfer_2"],
            "%интернет-банк%",
        )
        self.assertEqual(
            params["strict_transfer_exception_sber_narrative"],
            "%перевод денежных средств, наталья арменаковна ч из сбербанк%",
        )
        self.assertEqual(
            params["strict_transfer_exception_fee_1"],
            '%комиссия за оказание услуги "перевод с карты на карту"%',
        )
        self.assertEqual(
            params["strict_transfer_exception_fee_2"], "%sankt-pe, sankt-peterburg, ru%"
        )
        self.assertEqual(params["transfer_hint_like_1"], "%перевод%")
        self.assertEqual(params["transfer_hint_like_2"], "%сбп%")
        self.assertEqual(params["transfer_hint_like_8"], "%p2p%")


if __name__ == "__main__":
    unittest.main()
