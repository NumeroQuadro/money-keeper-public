from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from app.domain.transactions import fingerprint, normalize_row
from app.domain.transfers import TransferTx, score_transfer_pair
from app.services.pdf_extract import extract_pdf_text
from app.services.statement_parser import (
    _extract_merchant_label,
    _infer_yandex_bank_category,
    _normalize_ozon_description_for_cashflow,
    _normalize_sber_description_for_cashflow,
    _normalize_yandex_description_for_cashflow,
    _parse_ozon_records,
    _parse_spb_records,
    _parse_sber_records,
    parse_pdf_into_statements,
)


class PdfGoldenParserTests(unittest.TestCase):
    def _parse(self, file_name: str):
        root = Path(__file__).resolve().parents[3]
        pdf_path = root / "0_statements" / file_name
        if not pdf_path.exists():
            self.skipTest("Optional private golden PDF is not included in the public repository")

        pdf_text = extract_pdf_text(str(pdf_path))
        bundles = parse_pdf_into_statements(
            pdf_text=pdf_text,
            file_name=pdf_path.name,
            pdf_path=str(pdf_path),
        )
        self.assertGreaterEqual(len(bundles), 1)
        return bundles

    def test_ozon_provider_golden(self) -> None:
        bundles = self._parse("all_card_ozon.pdf")
        self.assertTrue(all(bundle.meta.provider == "ozon" for bundle in bundles))
        self.assertGreater(sum(len(bundle.txs) for bundle in bundles), 0)

    def test_sber_provider_golden(self) -> None:
        bundles = self._parse("sber_savings_golden.pdf")
        self.assertTrue(all(bundle.meta.provider == "sber" for bundle in bundles))
        self.assertEqual(bundles[0].meta.closing_balance, Decimal("3.35"))

    def test_sber_card_same_second_small_inflows_keep_distinct_references(self) -> None:
        bundles = self._parse("sber_card_golden.pdf")
        indexed_rows = {
            (bundle_idx, row.row_index): row
            for bundle_idx, bundle in enumerate(bundles)
            for row in bundle.rows
        }
        txs = [
            (bundle_idx, row_idx, tx)
            for bundle_idx, bundle in enumerate(bundles)
            for row_idx, tx in bundle.txs
        ]

        target = [
            (bundle_idx, row_idx, tx)
            for bundle_idx, row_idx, tx in txs
            if tx.direction == "in"
            and tx.amount == Decimal("26.00")
            and tx.operation_datetime is not None
            and tx.operation_datetime.strftime("%Y-%m-%d %H:%M") == "2025-02-26 17:48"
        ]
        self.assertEqual(len(target), 2)

        refs = {tx.bank_reference_id for _, _, tx in target}
        self.assertEqual(refs, {"689480", "199523"})

        descriptions = [tx.description_raw.lower() for _, _, tx in target]
        self.assertTrue(any("иван сергеевич" in desc for desc in descriptions))
        self.assertTrue(any("александр алексеевич" in desc for desc in descriptions))

        keys = set()
        for bundle_idx, row_idx, tx in target:
            row = indexed_rows[(bundle_idx, row_idx)]
            candidate = normalize_row(
                row=row,
                tx=tx,
                account_id="acc-sber-1004",
                statement_row_id=f"{bundle_idx}:{row_idx}",
            )
            keys.add(fingerprint(candidate))
        self.assertEqual(len(keys), 2)

    def test_yandex_provider_golden(self) -> None:
        bundles = self._parse("all_saving_yandex.pdf")
        self.assertTrue(all(bundle.meta.provider == "yandex" for bundle in bundles))
        self.assertGreater(sum(len(bundle.txs) for bundle in bundles), 0)

    def test_spb_provider_golden(self) -> None:
        bundles = self._parse("all_card_spb.pdf")
        self.assertTrue(all(bundle.meta.provider == "spb" for bundle in bundles))
        self.assertGreater(sum(len(bundle.txs) for bundle in bundles), 200)
        self.assertEqual(bundles[0].meta.opening_balance, Decimal("0.00"))
        self.assertEqual(bundles[0].meta.closing_balance, Decimal("0.00"))
        self.assertEqual(bundles[0].meta.total_credits, Decimal("524615.00"))
        self.assertEqual(bundles[0].meta.total_debits, Decimal("524615.00"))

        txs = [tx for bundle in bundles for _, tx in bundle.txs]
        self.assertTrue(any(tx.bank_category == "transfer" for tx in txs))
        self.assertTrue(any(bool(tx.bank_reference_id) for tx in txs))

    def test_spb_can_match_crossbank_transfer_with_ozon(self) -> None:
        spb_bundles = self._parse("all_card_spb.pdf")
        ozon_bundles = self._parse("all_card_ozon.pdf")
        spb_txs = [tx for bundle in spb_bundles for _, tx in bundle.txs]
        ozon_txs = [tx for bundle in ozon_bundles for _, tx in bundle.txs]

        spb_out = next(
            (
                tx
                for tx in spb_txs
                if tx.direction == "out"
                and tx.amount == Decimal("1500.00")
                and tx.operation_datetime is not None
                and tx.operation_datetime.date().isoformat() == "2025-12-19"
                and "озон банк" in tx.description_raw.lower()
            ),
            None,
        )
        self.assertIsNotNone(spb_out)

        ozon_in = next(
            (
                tx
                for tx in ozon_txs
                if tx.direction == "in"
                and tx.amount == Decimal("1500.00")
                and tx.operation_datetime is not None
                and tx.operation_datetime.date().isoformat() == "2025-12-19"
                and "иван иванович" in tx.description_raw.lower()
            ),
            None,
        )
        self.assertIsNotNone(ozon_in)

        assert spb_out is not None
        assert ozon_in is not None
        pair = score_transfer_pair(
            TransferTx(
                id="spb-out",
                account_id="acc-spb",
                direction=spb_out.direction,
                currency=spb_out.currency,
                amount_cents=int(spb_out.amount * 100),
                timestamp=spb_out.operation_datetime,
                description_raw=spb_out.description_raw,
                bank_category=spb_out.bank_category,
            ),
            TransferTx(
                id="ozon-in",
                account_id="acc-ozon",
                direction=ozon_in.direction,
                currency=ozon_in.currency,
                amount_cents=int(ozon_in.amount * 100),
                timestamp=ozon_in.operation_datetime,
                description_raw=ozon_in.description_raw,
                bank_category=ozon_in.bank_category,
            ),
        )
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertGreaterEqual(pair.score, 0.80)

    def test_multi_statement_pdf_golden(self) -> None:
        bundles = self._parse("all_card_yandex.pdf")
        self.assertGreaterEqual(len(bundles), 2)

    def test_yandex_card_operation_date_uses_preceding_context_lines(self) -> None:
        bundles = self._parse("all_card_yandex.pdf")
        rows = [row for bundle in bundles for row in bundle.rows]

        october_row = next(
            (
                row
                for row in rows
                if row.raw_text.startswith("01.11.2025 *1006")
                and "–199,00" in row.raw_text
                and row.direction == "out"
            ),
            None,
        )
        self.assertIsNotNone(october_row)
        assert october_row is not None
        self.assertIsNotNone(october_row.operation_date)
        assert october_row.operation_date is not None
        self.assertEqual(october_row.operation_date.isoformat(), "2025-10-31T22:03:00")

        november_row = next(
            (
                row
                for row in rows
                if row.raw_text.startswith("03.12.2025 *1006")
                and "–199,00" in row.raw_text
                and row.direction == "out"
            ),
            None,
        )
        self.assertIsNotNone(november_row)
        assert november_row is not None
        self.assertIsNotNone(november_row.operation_date)
        assert november_row.operation_date is not None
        self.assertEqual(november_row.operation_date.isoformat(), "2025-11-30T21:59:00")

    def test_overlap_pdf_golden(self) -> None:
        all_bundles = self._parse("sber_card_full_golden.pdf")
        month_bundles = self._parse("sber_card_month_golden.pdf")

        def keys(bundles):
            out = set()
            for bundle in bundles:
                for _, tx in bundle.txs:
                    out.add(
                        (tx.operation_datetime, tx.amount, tx.direction, tx.description_raw[:40])
                    )
            return out

        overlap = keys(all_bundles) & keys(month_bundles)
        self.assertGreater(len(overlap), 0)

    def test_ozon_prefers_signed_ruble_amount_when_noise_numbers_present(self) -> None:
        pages = [
            (
                1,
                "24.01.2026 00:41:08 5000000001 Перевод AS1F006K056KFADA9879LSBKRO1V0H74 "
                "через СБП. 1004034,1004034007,12.1143.7,2026-01-24T00:40:41.000. "
                'Получатель: ООО "ТК ПРОГРЕСС". Без НДС. - 59.98 ₽ - 59.98 ₽',
            )
        ]
        records = _parse_ozon_records(pages)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].amount, Decimal("59.98"))
        self.assertEqual(records[0].direction, "out")

    def test_ozon_card_payment_uses_signed_tail_amount_not_unsigned_sum_token(self) -> None:
        pages = [
            (
                1,
                "28.01.2026 19:31:51 8453838316 Оплата товаров по карте 6569 сумма 1161.47 "
                "в PYATEROCHKA 20572 SANKT-PETERBU RU дата 2026-01-28 время 19:31:51 "
                "- 1 161.47 ₽ - 1 161.47 ₽",
            )
        ]
        records = _parse_ozon_records(pages)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].amount, Decimal("1161.47"))
        self.assertEqual(records[0].direction, "out")

    def test_ozon_transfer_worded_merchant_outflow_is_normalized(self) -> None:
        normalized = _normalize_ozon_description_for_cashflow(
            description_raw=(
                "Перевод AS1R006ES4V9RCVL8VOB7TDQA8HE8673 через СБП. {TC5}<31YY>. "
                'Получатель: ООО "АГРОТОРГ". Без НДС.'
            ),
            direction="out",
        )
        self.assertTrue(normalized.startswith("Оплата AS1R006ES4V9RCVL8VOB7TDQA8HE8673"))
        self.assertNotIn("Перевод", normalized)
        self.assertNotIn("СБП", normalized)

    def test_ozon_alifmobi_and_eds_inflows_drop_transfer_keyword(self) -> None:
        alifmobi = _normalize_ozon_description_for_cashflow(
            description_raw=(
                "Зачисление по переводу денежных средств по карте 6569 сумма из ALIFMOBI6 "
                "MOSKVA RU дата 2025-10-02 время 21:33:46"
            ),
            direction="in",
        )
        self.assertNotIn("по переводу денежных средств", alifmobi)
        self.assertIn("по зачислению денежных средств", alifmobi)

        eds = _normalize_ozon_description_for_cashflow(
            description_raw=(
                "Перевод остатка ЭДС в связи повышением уровня идентификации. Без НДС."
            ),
            direction="in",
        )
        self.assertTrue(eds.startswith("Зачисление остатка ЭДС"))
        self.assertNotIn("Перевод остатка", eds)

    def test_yandex_sbp_qr_wording_is_normalized_for_outflows(self) -> None:
        normalized = _normalize_yandex_description_for_cashflow(
            description_raw="Оплата СБП QR (31YY Пятерочка)",
            direction="out",
        )
        self.assertEqual(normalized, "Оплата QR (31YY Пятерочка)")

    def test_sber_refund_qr_sbp_wording_is_normalized_for_inflows(self) -> None:
        normalized = _normalize_sber_description_for_cashflow(
            description_raw="12:19 Возврат покупки по QR–коду СБП +900,00",
            direction="in",
        )
        self.assertIn("Возврат покупки по QR–коду", normalized)
        self.assertNotIn("СБП", normalized)

    def test_yandex_wallet_to_bank_transfer_sets_transfer_bank_category(self) -> None:
        bank_category = _infer_yandex_bank_category(
            description_raw="Перенос денежных средств с ЭДС на банковский счёт"
        )
        self.assertEqual(bank_category, "transfer")

    def test_spb_duplicate_rows_receive_distinct_synthetic_references(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        '03.11.2022 ПАО "БАНК "САНКТ-ПЕТЕРБУРГ" 47422810000000000002 '
                        "Зачисление зарплаты от КОМПАНИЯ ПРИМЕР. 5 000.00",
                        '03.11.2022 ПАО "БАНК "САНКТ-ПЕТЕРБУРГ" 47422810000000000002 '
                        "Зачисление зарплаты от КОМПАНИЯ ПРИМЕР. 5 000.00",
                    ]
                ),
            )
        ]

        first = _parse_spb_records(pages)
        second = _parse_spb_records(pages)

        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        refs_first = [record.bank_reference_id for record in first]
        refs_second = [record.bank_reference_id for record in second]
        self.assertTrue(all(refs_first))
        self.assertEqual(refs_first, refs_second)
        self.assertNotEqual(refs_first[0], refs_first[1])

    def test_spb_hidden_time_cue_sets_inferred_precision_and_evidence(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        '04.07.2024 PAO "Bank "Sankt-Pe, Sankt-Peterburg, RU',
                        "Перевод с карты на карту. НДС не облагается. .",
                        "04.07.2024 20:34 карта *1005",
                        "-3 000.00",
                    ]
                ),
            )
        ]

        records = _parse_spb_records(pages)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].operation_datetime.isoformat(), "2024-07-04T20:34:00")
        self.assertEqual(records[0].timestamp_precision, "inferred")
        self.assertEqual(records[0].raw_data.get("timestamp_precision"), "inferred")
        evidence = records[0].raw_data.get("timestamp_evidence")
        self.assertIsInstance(evidence, dict)
        assert isinstance(evidence, dict)
        self.assertIn("method", evidence)
        self.assertIn("matched_time", evidence)

    def test_spb_without_time_cue_stays_date_only_precision(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        '04.07.2024 PAO "Bank "Sankt-Pe, Sankt-Peterburg, RU',
                        "Перевод с карты на карту. НДС не облагается. .",
                        "-3 000.00",
                    ]
                ),
            )
        ]

        records = _parse_spb_records(pages)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].operation_datetime.isoformat(), "2024-07-04T00:00:00")
        self.assertEqual(records[0].timestamp_precision, "date_only")
        self.assertEqual(records[0].raw_data.get("timestamp_precision"), "date_only")
        self.assertNotIn("timestamp_evidence", records[0].raw_data)

    def test_sber_card_continuation_lines_capture_auth_and_counterparty(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        "Расшифровка операций",
                        "25.12.2025 23:49 Перевод СБП +408,00",
                        "25.12.2025 951498 Перевод от И. Иван Иванович. Операция по счету ****1002",
                        "25.12.2025 23:17 Перевод с карты 2 181,00",
                        "25.12.2025 093645 Перевод для И. Иван Иванович. Операция по счету ****1002",
                    ]
                ),
            )
        ]

        records = _parse_sber_records(pages, statement_type="card")
        self.assertEqual(len(records), 2)

        self.assertEqual(records[0].bank_reference_id, "951498")
        self.assertIn("перевод от и. иван иванович", records[0].description_raw.lower())
        self.assertEqual(records[0].bank_category, "transfer")
        self.assertEqual(records[0].direction, "in")

        self.assertEqual(records[1].bank_reference_id, "093645")
        self.assertIn("перевод для и. иван иванович", records[1].description_raw.lower())
        self.assertEqual(records[1].bank_category, "transfer")
        self.assertEqual(records[1].direction, "out")

    def test_sber_card_alphanumeric_continuation_auth_preserves_counterparty_text(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        "Расшифровка операций",
                        "26.02.2025 17:48 Перевод СБП +26,00",
                        "26.02.2025 SBP689480 Перевод от С. Иван Сергеевич. Операция по счету ****1002",
                        "26.02.2025 17:48 Перевод СБП +26,00",
                        "26.02.2025 SBP199523 Перевод от З. Александр Алексеевич. Операция по счету ****1002",
                    ]
                ),
            )
        ]

        records = _parse_sber_records(pages, statement_type="card")
        self.assertEqual(len(records), 2)

        self.assertEqual(records[0].bank_reference_id, "SBP689480")
        self.assertIn("иван сергеевич", records[0].description_raw.lower())
        self.assertIn("sbp689480", records[0].raw_text.lower())

        self.assertEqual(records[1].bank_reference_id, "SBP199523")
        self.assertIn("александр алексеевич", records[1].description_raw.lower())
        self.assertIn("sbp199523", records[1].raw_text.lower())

    def test_sber_card_unsigned_amounts_use_text_direction_markers(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        "Расшифровка операций",
                        "25.12.2025 23:49 Перевод от И. Иван Иванович 408,00",
                        "25.12.2025 23:17 Перевод для И. Иван Иванович 2181,00",
                        "25.12.2025 22:40 Оплата по QR-коду СБП 750,00",
                        "25.12.2025 22:41 SBERBANK ONL@IN VKLAD-KARTA. Операция по карте ****1004 550,38",
                        "25.12.2025 22:42 SBERBANK ONL@IN KARTA-VKLAD. Операция по карте ****1004 550,38",
                        "06.07.2025 18:49 Перевод с карты 2 800,00 T-Bank Card2Card перевод на карту 5189****1003",
                    ]
                ),
            )
        ]

        records = _parse_sber_records(pages, statement_type="card")
        self.assertEqual(len(records), 6)
        self.assertEqual(
            [record.direction for record in records],
            ["in", "out", "out", "in", "out", "out"],
        )

    def test_sber_qr_refund_rows_are_not_marked_transfer(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        "Расшифровка операций",
                        "07.06.2025 12:19 Возврат покупки по QR–коду СБП +900,00",
                    ]
                ),
            )
        ]

        records = _parse_sber_records(pages, statement_type="card")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].direction, "in")
        self.assertEqual(records[0].bank_category, "refund")
        self.assertNotIn("СБП", records[0].description_raw)

    def test_sber_savings_rows_capture_transfer_cues_and_inline_reference(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        "Расшифровка операций",
                        "11.09.2025 Зачисление к/с 40817 810 0 0000 0000001 02, № 100000000001-185 +5 000,00 154 575,73",
                        "09.09.2025 Списание к/с 40817 810 0 0000 0000001 03, № 100000000001-184 -1 400,00 149 575,73",
                        "31.08.2025 Капитализация вклада +0,02 3,35",
                    ]
                ),
            )
        ]

        records = _parse_sber_records(pages, statement_type="savings")
        self.assertEqual(len(records), 3)

        self.assertEqual(records[0].bank_category, "transfer")
        self.assertEqual(records[0].bank_reference_id, "100000000001-185")
        self.assertEqual(records[0].direction, "in")

        self.assertEqual(records[1].bank_category, "transfer")
        self.assertEqual(records[1].bank_reference_id, "100000000001-184")
        self.assertEqual(records[1].direction, "out")

        self.assertEqual(records[2].bank_category, "")

    def test_sber_savings_multiline_rows_keep_continuation_detail_text(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        "Расшифровка операций",
                        "16.09.2024 Зачисление",
                        "к/с 40817 810 1 0000 0000002 02, № 100000000001-52 +1 000,00 103 984,00",
                        "16.09.2024 Списание",
                        "к/с 40817 810 1 0000 0000002 03, № 100000000001-51 -500,00 103 484,00",
                    ]
                ),
            )
        ]

        records = _parse_sber_records(pages, statement_type="savings")
        self.assertEqual(len(records), 2)

        self.assertEqual(records[0].direction, "in")
        self.assertEqual(records[0].bank_category, "transfer")
        self.assertEqual(records[0].bank_reference_id, "100000000001-52")
        self.assertIn("к/с", records[0].description_raw.lower())
        self.assertIn("№ 100000000001-52", records[0].description_raw)

        self.assertEqual(records[1].direction, "out")
        self.assertEqual(records[1].bank_category, "transfer")
        self.assertEqual(records[1].bank_reference_id, "100000000001-51")
        self.assertIn("к/с", records[1].description_raw.lower())
        self.assertIn("№ 100000000001-51", records[1].description_raw)

    def test_extract_merchant_label_from_ozon_style_description(self) -> None:
        merchant = _extract_merchant_label(
            description_raw=(
                "Оплата товаров по карте 6569 сумма 1161.47 в PYATEROCHKA 20572 "
                "SANKT-PETERBU RU дата 2026-01-28 время 19:31:51 - 1 161.47 ₽"
            ),
            bank_category="",
        )
        self.assertEqual(merchant, "PYATEROCHKA 20572 SANKT-PETERBU RU")

    def test_extract_merchant_label_from_company_prefix(self) -> None:
        merchant = _extract_merchant_label(
            description_raw='Оплата услуг ООО "АГРОТОРГ" по договору 1234',
            bank_category="",
        )
        self.assertEqual(merchant, "ООО АГРОТОРГ")

    def test_extract_merchant_label_skips_transfer_prefixes(self) -> None:
        merchant = _extract_merchant_label(
            description_raw="Перевод по СБП, НДС не облагается",
            bank_category="transfer",
        )
        self.assertEqual(merchant, "")

    def test_extract_merchant_label_inflow_sender_field(self) -> None:
        merchant = _extract_merchant_label(
            description_raw=(
                "Перевод A5320213306467040000000011630701 через СБП. "
                "Отправитель: Иван Иванович И. Без НДС."
            ),
            bank_category="",
            direction="in",
        )
        self.assertEqual(merchant, "Иван Иванович И")

    def test_extract_merchant_label_inflow_incoming_sbp(self) -> None:
        merchant = _extract_merchant_label(
            description_raw=("Входящий перевод СБП, Иван Иванович И., +7 921 000-00-00, Сбербанк"),
            bank_category="transfer",
            direction="in",
        )
        self.assertEqual(merchant, "Иван Иванович И")

    def test_extract_merchant_label_inflow_salary_source(self) -> None:
        merchant = _extract_merchant_label(
            description_raw=(
                'ПАО "БАНК "САНКТ-ПЕТЕРБУРГ" 47422810000000000002 '
                "Зачисление зарплаты от КОМПАНИЯ ПРИМЕР."
            ),
            bank_category="salary",
            direction="in",
        )
        self.assertEqual(merchant, "КОМПАНИЯ ПРИМЕР")

    def test_extract_merchant_label_inflow_leading_person_account(self) -> None:
        merchant = _extract_merchant_label(
            description_raw=(
                "ИВАНОВА АННА ИВАНОВНА 40817810000000000003 "
                "Перевод денежных средств НДС не облагается. Интернет-банк"
            ),
            bank_category="transfer",
            direction="in",
        )
        self.assertEqual(merchant, "ИВАНОВА АННА ИВАНОВНА")

    def test_extract_merchant_label_inflow_capitalization_of_deposit(self) -> None:
        merchant = _extract_merchant_label(
            description_raw="Капитализация вклада 08, № 254130-197 +0,02 3,35",
            bank_category="transfer",
            direction="in",
        )
        self.assertEqual(merchant, "Капитализация вклада")

    def test_extract_merchant_label_inflow_correspondent_account_credit(self) -> None:
        merchant = _extract_merchant_label(
            description_raw=(
                "Зачисление к/с 30233 810 3 0000 0000004 02, № 100000000002-81 +600,00 61 600,00"
            ),
            bank_category="transfer",
            direction="in",
        )
        self.assertEqual(merchant, "Зачисление к/с")

    def test_extract_merchant_label_inflow_ozon_eds_remainder(self) -> None:
        merchant = _extract_merchant_label(
            description_raw=(
                "Зачисление остатка ЭДС в связи повышением уровня идентификации. Без НДС."
            ),
            bank_category="",
            direction="in",
        )
        self.assertEqual(merchant, "Остаток ЭДС")


if __name__ == "__main__":
    unittest.main()
