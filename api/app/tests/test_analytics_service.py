from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Transaction
from app.services.analytics import (
    get_income_breakdown,
    get_monthly_flow,
    get_spend_mix,
    get_top_merchants,
)
from app.tests.db_test_utils import get_test_engine


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = get_test_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self._Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def test_monthly_flow_respects_transfer_toggle(self) -> None:
        with self._Session() as db:
            db.add_all(
                [
                    Transaction(
                        amount=100,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 1, 10, 0, 0),
                        category="Groceries",
                        meaning="spend",
                    ),
                    Transaction(
                        amount=300,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 2, 10, 0, 0),
                        meaning="salary",
                    ),
                    Transaction(
                        amount=80,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 3, 10, 0, 0),
                        meaning="internal_transfer",
                    ),
                    Transaction(
                        amount=80,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 3, 10, 1, 0),
                        meaning="internal_transfer",
                    ),
                    Transaction(
                        amount=50,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 2, 1, 10, 0, 0),
                        category="Transport",
                        meaning="spend",
                    ),
                ]
            )
            db.commit()

            without_transfers = get_monthly_flow(db)
            with_transfers = get_monthly_flow(db, include_transfers=True)

        self.assertEqual(
            without_transfers["items"],
            [
                {
                    "period": "2026-01",
                    "inflow": 300.0,
                    "outflow": 100.0,
                    "net": 200.0,
                    "tx_count": 2,
                },
                {"period": "2026-02", "inflow": 0.0, "outflow": 50.0, "net": -50.0, "tx_count": 1},
            ],
        )
        self.assertEqual(
            with_transfers["items"],
            [
                {
                    "period": "2026-01",
                    "inflow": 380.0,
                    "outflow": 180.0,
                    "net": 200.0,
                    "tx_count": 4,
                },
                {"period": "2026-02", "inflow": 0.0, "outflow": 50.0, "net": -50.0, "tx_count": 1},
            ],
        )

    def test_monthly_flow_supports_strict_transfer_like_lens(self) -> None:
        with self._Session() as db:
            db.add_all(
                [
                    Transaction(
                        amount=100,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 1, 10, 0, 0),
                        category="Groceries",
                        meaning="spend",
                    ),
                    Transaction(
                        amount=300,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 2, 10, 0, 0),
                        meaning="salary",
                    ),
                    Transaction(
                        amount=70,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 3, 11, 0, 0),
                        meaning="external_transfer",
                        description_raw="Transfer to card",
                    ),
                    Transaction(
                        amount=40,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 3, 12, 0, 0),
                        meaning="unknown",
                        description_raw="Перевод по СБП другу",
                    ),
                    Transaction(
                        amount=80,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 3, 13, 0, 0),
                        meaning="internal_transfer",
                    ),
                    Transaction(
                        amount=80,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 3, 13, 1, 0),
                        meaning="internal_transfer",
                    ),
                ]
            )
            db.commit()

            default_lens = get_monthly_flow(db)
            strict_lens = get_monthly_flow(db, cashflow_lens="strict_transfer_like")
            include_all = get_monthly_flow(
                db,
                include_transfers=True,
                cashflow_lens="strict_transfer_like",
            )

        self.assertEqual(default_lens["cashflow_lens"], "internal_only")
        self.assertEqual(strict_lens["cashflow_lens"], "strict_transfer_like")
        self.assertEqual(
            default_lens["items"],
            [
                {
                    "period": "2026-01",
                    "inflow": 300.0,
                    "outflow": 210.0,
                    "net": 90.0,
                    "tx_count": 4,
                }
            ],
        )
        self.assertEqual(
            strict_lens["items"],
            [
                {
                    "period": "2026-01",
                    "inflow": 300.0,
                    "outflow": 100.0,
                    "net": 200.0,
                    "tx_count": 2,
                }
            ],
        )
        self.assertEqual(
            include_all["items"],
            [
                {
                    "period": "2026-01",
                    "inflow": 380.0,
                    "outflow": 290.0,
                    "net": 90.0,
                    "tx_count": 6,
                }
            ],
        )

    def test_monthly_flow_supports_high_confidence_transfer_like_lens(self) -> None:
        with self._Session() as db:
            db.add_all(
                [
                    Transaction(
                        amount=100,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 3, 1, 10, 0, 0),
                        category="Groceries",
                        meaning="spend",
                    ),
                    Transaction(
                        amount=300,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 3, 1, 11, 0, 0),
                        meaning="salary",
                    ),
                    Transaction(
                        amount=200,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 3, 1, 12, 0, 0),
                        meaning="unknown",
                        bank_category="transfer",
                        bank_reference_id="SBP111111",
                        description_raw="Перевод с карты 200,00 Перевод для И. Иван Иванович",
                    ),
                    Transaction(
                        amount=120,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 3, 1, 12, 1, 0),
                        meaning="unknown",
                        bank_category="transfer",
                        bank_reference_id="SBP222222",
                        description_raw="Перевод на карту +120,00 Перевод от И. Иван Иванович",
                    ),
                    Transaction(
                        amount=105,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 3, 1, 12, 2, 0),
                        meaning="unknown",
                        bank_category="transfer",
                        bank_reference_id="SBP777777",
                        description_raw="Перевод сбп +105,00 Перевод от И. Иван Иванович",
                    ),
                    Transaction(
                        amount=50,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 3, 1, 13, 0, 0),
                        meaning="unknown",
                        bank_category="transfer",
                        bank_reference_id="SBP333333",
                        description_raw="Перевод с карты 50,00 Перевод для И. Иван Иванович",
                    ),
                    Transaction(
                        amount=40,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 3, 1, 14, 0, 0),
                        meaning="unknown",
                        description_raw="Перевод по СБП другу",
                    ),
                    Transaction(
                        amount=80,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 3, 1, 15, 0, 0),
                        meaning="internal_transfer",
                    ),
                    Transaction(
                        amount=80,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 3, 1, 15, 1, 0),
                        meaning="internal_transfer",
                    ),
                ]
            )
            db.commit()

            default_lens = get_monthly_flow(db)
            high_conf_lens = get_monthly_flow(
                db,
                cashflow_lens="high_confidence_transfer_like",
            )
            strict_lens = get_monthly_flow(db, cashflow_lens="strict_transfer_like")
            include_all = get_monthly_flow(
                db,
                include_transfers=True,
                cashflow_lens="high_confidence_transfer_like",
            )

        self.assertEqual(default_lens["cashflow_lens"], "internal_only")
        self.assertEqual(high_conf_lens["cashflow_lens"], "high_confidence_transfer_like")
        self.assertEqual(strict_lens["cashflow_lens"], "strict_transfer_like")
        self.assertEqual(
            default_lens["items"],
            [
                {
                    "period": "2026-03",
                    "inflow": 525.0,
                    "outflow": 390.0,
                    "net": 135.0,
                    "tx_count": 7,
                }
            ],
        )
        self.assertEqual(
            high_conf_lens["items"],
            [
                {
                    "period": "2026-03",
                    "inflow": 525.0,
                    "outflow": 140.0,
                    "net": 385.0,
                    "tx_count": 5,
                }
            ],
        )
        self.assertEqual(
            strict_lens["items"],
            [
                {
                    "period": "2026-03",
                    "inflow": 300.0,
                    "outflow": 100.0,
                    "net": 200.0,
                    "tx_count": 2,
                }
            ],
        )
        self.assertEqual(
            include_all["items"],
            [
                {
                    "period": "2026-03",
                    "inflow": 605.0,
                    "outflow": 470.0,
                    "net": 135.0,
                    "tx_count": 9,
                }
            ],
        )

    def test_spend_mix_income_breakdown_and_top_merchants(self) -> None:
        with self._Session() as db:
            db.add_all(
                [
                    Transaction(
                        amount=40,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 1, 10, 0, 0),
                        category="Groceries",
                        merchant_normalized="Store A",
                        meaning="spend",
                    ),
                    Transaction(
                        amount=20,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 2, 10, 0, 0),
                        category="",
                        merchant_normalized="",
                        bank_category="Cash",
                        meaning="spend",
                    ),
                    Transaction(
                        amount=5,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 3, 10, 0, 0),
                        meaning="interest",
                    ),
                    Transaction(
                        amount=2,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 4, 10, 0, 0),
                        meaning="cashback",
                    ),
                    Transaction(
                        amount=50,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 5, 10, 0, 0),
                        meaning="income",
                    ),
                    Transaction(
                        amount=99,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 1, 6, 10, 0, 0),
                        meaning="internal_transfer",
                        category="Transfer",
                        merchant_normalized="Own transfer",
                    ),
                ]
            )
            db.commit()

            spend_mix = get_spend_mix(db, limit=10)
            income_breakdown = get_income_breakdown(db)
            top_merchants = get_top_merchants(db, limit=10)

        self.assertEqual(
            spend_mix["items"],
            [
                {"category": "Groceries", "spent": 40.0, "tx_count": 1},
                {"category": "Uncategorized", "spent": 20.0, "tx_count": 1},
            ],
        )
        self.assertEqual(
            income_breakdown["items"],
            [
                {"income_bucket": "other", "income": 50.0, "tx_count": 1},
                {"income_bucket": "interest", "income": 5.0, "tx_count": 1},
                {"income_bucket": "cashback", "income": 2.0, "tx_count": 1},
            ],
        )
        self.assertEqual(
            top_merchants["items"],
            [
                {"merchant": "Store A", "spent": 40.0, "tx_count": 1},
                {"merchant": "Cash", "spent": 20.0, "tx_count": 1},
            ],
        )

    def test_secondary_lens_applies_to_spend_income_and_merchants(self) -> None:
        with self._Session() as db:
            db.add_all(
                [
                    Transaction(
                        amount=40,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 2, 1, 10, 0, 0),
                        category="Groceries",
                        merchant_normalized="Store A",
                        meaning="spend",
                    ),
                    Transaction(
                        amount=30,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 2, 1, 11, 0, 0),
                        category="Transfers",
                        merchant_normalized="Friend",
                        description_raw="Перевод по СБП другу",
                        meaning="unknown",
                    ),
                    Transaction(
                        amount=60,
                        direction="out",
                        currency="RUB",
                        operation_datetime=datetime(2026, 2, 1, 12, 0, 0),
                        category="Transfers",
                        merchant_normalized="Card2Card",
                        meaning="external_transfer",
                    ),
                    Transaction(
                        amount=100,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 2, 1, 13, 0, 0),
                        meaning="salary",
                    ),
                    Transaction(
                        amount=20,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 2, 1, 14, 0, 0),
                        meaning="external_transfer",
                    ),
                    Transaction(
                        amount=50,
                        direction="in",
                        currency="RUB",
                        operation_datetime=datetime(2026, 2, 1, 15, 0, 0),
                        meaning="unknown",
                        description_raw="Входящий перевод СБП, Иван Иванович",
                        bank_category="transfer",
                    ),
                ]
            )
            db.commit()

            default_spend = get_spend_mix(db, limit=10)
            strict_spend = get_spend_mix(db, limit=10, cashflow_lens="strict_transfer_like")
            default_income = get_income_breakdown(db)
            strict_income = get_income_breakdown(db, cashflow_lens="strict_transfer_like")
            default_merchants = get_top_merchants(db, limit=10)
            strict_merchants = get_top_merchants(
                db,
                limit=10,
                cashflow_lens="strict_transfer_like",
            )

        self.assertEqual(sum(item["tx_count"] for item in default_spend["items"]), 3)
        self.assertEqual(sum(item["spent"] for item in default_spend["items"]), 130.0)
        self.assertEqual(sum(item["tx_count"] for item in strict_spend["items"]), 1)
        self.assertEqual(sum(item["spent"] for item in strict_spend["items"]), 40.0)

        self.assertEqual(sum(item["tx_count"] for item in default_income["items"]), 3)
        self.assertEqual(sum(item["income"] for item in default_income["items"]), 170.0)
        self.assertEqual(sum(item["tx_count"] for item in strict_income["items"]), 1)
        self.assertEqual(sum(item["income"] for item in strict_income["items"]), 100.0)

        self.assertEqual(sum(item["tx_count"] for item in default_merchants["items"]), 3)
        self.assertEqual(sum(item["spent"] for item in default_merchants["items"]), 130.0)
        self.assertEqual(sum(item["tx_count"] for item in strict_merchants["items"]), 1)
        self.assertEqual(sum(item["spent"] for item in strict_merchants["items"]), 40.0)


if __name__ == "__main__":
    unittest.main()
