from __future__ import annotations

from decimal import Decimal
from typing import Literal, TypeAlias

CASHFLOW_LENS_INTERNAL_ONLY = "internal_only"
CASHFLOW_LENS_HIGH_CONFIDENCE_TRANSFER_LIKE = "high_confidence_transfer_like"
CASHFLOW_LENS_STRICT_TRANSFER_LIKE = "strict_transfer_like"
DEFAULT_CASHFLOW_LENS = CASHFLOW_LENS_INTERNAL_ONLY

CashflowLens: TypeAlias = Literal[
    "internal_only",
    "high_confidence_transfer_like",
    "strict_transfer_like",
]

CASHFLOW_LENS_VALUES: tuple[str, ...] = (
    CASHFLOW_LENS_INTERNAL_ONLY,
    CASHFLOW_LENS_HIGH_CONFIDENCE_TRANSFER_LIKE,
    CASHFLOW_LENS_STRICT_TRANSFER_LIKE,
)

INTERNAL_TRANSFER_MEANING = "internal_transfer"
EXTERNAL_TRANSFER_MEANING = "external_transfer"
TRANSFER_BANK_CATEGORY = "transfer"
HIGH_CONFIDENCE_TRANSFER_MIN_IN_AMOUNT = Decimal("185.00")
HIGH_CONFIDENCE_TRANSFER_MIN_OUT_AMOUNT = Decimal("50.00")
# Backward-compatible alias used by some reporting tooling/tests.
# Keep this stable for older consumers that still expect a single threshold.
HIGH_CONFIDENCE_TRANSFER_MIN_AMOUNT = Decimal("100.00")
HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_IN_PATTERNS: tuple[str, ...] = (
    "%перевод сбп%",
    "%через сбп%",
    "%зачисление к/с%",
    "%отправитель:%",
)
HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_OUT_PATTERNS: tuple[str, ...] = (
    "%перевод для%",
    "%перевод для%",
    "%перевод для%",
)
# Backward-compatible aggregate sequence used by metrics tooling/tests.
HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_PATTERNS: tuple[str, ...] = (
    *HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_IN_PATTERNS,
    *HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_OUT_PATTERNS,
)
TRANSFER_HINT_LIKE_PATTERNS: tuple[str, ...] = (
    "%перевод%",
    "%сбп%",
    "%sbp%",
    "%между своими%",
    "%собственн%",
    "%transfer%",
    "%between accounts%",
    "%p2p%",
)
STRICT_TRANSFER_ACCOUNT_FLOW_PATTERNS: tuple[str, ...] = (
    "%списание к/с%",
    "%зачисление к/с%",
)
STRICT_TRANSFER_HINT_LIKE_PATTERNS: tuple[str, ...] = (
    "%перевод%",
    "%между своими%",
    "%собственн%",
    "%transfer%",
    "%between accounts%",
    "%p2p%",
    "%платеж сбп%",
)
STRICT_TRANSFER_HINT_EXCEPTION_PERSONAL_TRANSFER_PATTERNS: tuple[str, ...] = (
    "%перевод денежных средств%",
    "%интернет-банк%",
)
STRICT_TRANSFER_HINT_EXCEPTION_SBER_NARRATIVE_PATTERN = (
    "%перевод денежных средств, наталья арменаковна ч из сбербанк%"
)
STRICT_TRANSFER_HINT_EXCEPTION_FEE_PATTERNS: tuple[str, ...] = (
    '%комиссия за оказание услуги "перевод с карты на карту"%',
    "%sankt-pe, sankt-peterburg, ru%",
)


def normalize_cashflow_lens(value: str | None) -> str:
    candidate = (value or "").strip()
    if candidate in CASHFLOW_LENS_VALUES:
        return candidate
    return DEFAULT_CASHFLOW_LENS


def transfer_exclusion_predicate_sql(*, alias: str, cashflow_lens: str) -> str:
    normalized_lens = normalize_cashflow_lens(cashflow_lens)
    meaning = f"coalesce({alias}.meaning, '')"
    direction = f"coalesce({alias}.direction, '')"
    bank_category = f"lower(coalesce({alias}.bank_category, ''))"
    bank_reference = f"coalesce({alias}.bank_reference_id, '')"
    amount = f"coalesce({alias}.amount, 0)"
    description = f"lower(coalesce({alias}.description_raw, ''))"

    if normalized_lens == CASHFLOW_LENS_STRICT_TRANSFER_LIKE:
        strict_hint_expression = (
            f"{description} like :strict_transfer_like_1 "
            f"or {description} like :strict_transfer_like_2 "
            f"or {description} like :strict_transfer_like_3 "
            f"or {description} like :strict_transfer_like_4 "
            f"or {description} like :strict_transfer_like_5 "
            f"or {description} like :strict_transfer_like_6 "
            f"or {description} like :strict_transfer_like_7"
        )
        strict_exception_expression = (
            f"(({description} like :strict_transfer_exception_personal_transfer_1 "
            f"and {description} like :strict_transfer_exception_personal_transfer_2) "
            f"or ({description} like :strict_transfer_exception_sber_narrative) "
            f"or ({description} like :strict_transfer_exception_fee_1 "
            f"and {description} like :strict_transfer_exception_fee_2))"
        )
        return (
            f"{meaning} = :internal_transfer_meaning "
            f"or {meaning} = :external_transfer_meaning "
            f"or {description} like :strict_transfer_account_flow_1 "
            f"or {description} like :strict_transfer_account_flow_2 "
            f"or (({strict_hint_expression}) and not {strict_exception_expression})"
        )

    if normalized_lens == CASHFLOW_LENS_HIGH_CONFIDENCE_TRANSFER_LIKE:
        return (
            f"{meaning} = :internal_transfer_meaning "
            f"or {meaning} = :external_transfer_meaning "
            f"or ("
            f"{bank_category} = :transfer_bank_category "
            f"and {bank_reference} <> '' "
            f"and (("
            f"{direction} = 'in' "
            f"and {amount} >= :high_conf_transfer_min_in_amount "
            f"and ("
            f"{description} like :high_conf_transfer_like_in_1 "
            f"or {description} like :high_conf_transfer_like_in_2 "
            f"or {description} like :high_conf_transfer_like_in_3 "
            f"or {description} like :high_conf_transfer_like_in_4"
            f")"
            f") or ("
            f"{direction} <> 'in' "
            f"and {amount} >= :high_conf_transfer_min_out_amount "
            f"and ("
            f"{description} like :high_conf_transfer_like_out_1 "
            f"or {description} like :high_conf_transfer_like_out_2 "
            f"or {description} like :high_conf_transfer_like_out_3"
            f")"
            f"))"
            f")"
        )

    return f"{meaning} = :internal_transfer_meaning"


def transfer_exclusion_params() -> dict[str, str | Decimal]:
    return {
        "internal_transfer_meaning": INTERNAL_TRANSFER_MEANING,
        "external_transfer_meaning": EXTERNAL_TRANSFER_MEANING,
        "transfer_bank_category": TRANSFER_BANK_CATEGORY,
        "high_conf_transfer_min_in_amount": HIGH_CONFIDENCE_TRANSFER_MIN_IN_AMOUNT,
        "high_conf_transfer_min_out_amount": HIGH_CONFIDENCE_TRANSFER_MIN_OUT_AMOUNT,
        "high_conf_transfer_min_amount": HIGH_CONFIDENCE_TRANSFER_MIN_AMOUNT,
        "high_conf_transfer_like_in_1": HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_IN_PATTERNS[0],
        "high_conf_transfer_like_in_2": HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_IN_PATTERNS[1],
        "high_conf_transfer_like_in_3": HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_IN_PATTERNS[2],
        "high_conf_transfer_like_in_4": HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_IN_PATTERNS[3],
        "high_conf_transfer_like_out_1": HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_OUT_PATTERNS[0],
        "high_conf_transfer_like_out_2": HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_OUT_PATTERNS[1],
        "high_conf_transfer_like_out_3": HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_OUT_PATTERNS[2],
        "strict_transfer_account_flow_1": STRICT_TRANSFER_ACCOUNT_FLOW_PATTERNS[0],
        "strict_transfer_account_flow_2": STRICT_TRANSFER_ACCOUNT_FLOW_PATTERNS[1],
        "strict_transfer_like_1": STRICT_TRANSFER_HINT_LIKE_PATTERNS[0],
        "strict_transfer_like_2": STRICT_TRANSFER_HINT_LIKE_PATTERNS[1],
        "strict_transfer_like_3": STRICT_TRANSFER_HINT_LIKE_PATTERNS[2],
        "strict_transfer_like_4": STRICT_TRANSFER_HINT_LIKE_PATTERNS[3],
        "strict_transfer_like_5": STRICT_TRANSFER_HINT_LIKE_PATTERNS[4],
        "strict_transfer_like_6": STRICT_TRANSFER_HINT_LIKE_PATTERNS[5],
        "strict_transfer_like_7": STRICT_TRANSFER_HINT_LIKE_PATTERNS[6],
        "strict_transfer_exception_personal_transfer_1": (
            STRICT_TRANSFER_HINT_EXCEPTION_PERSONAL_TRANSFER_PATTERNS[0]
        ),
        "strict_transfer_exception_personal_transfer_2": (
            STRICT_TRANSFER_HINT_EXCEPTION_PERSONAL_TRANSFER_PATTERNS[1]
        ),
        "strict_transfer_exception_sber_narrative": (
            STRICT_TRANSFER_HINT_EXCEPTION_SBER_NARRATIVE_PATTERN
        ),
        "strict_transfer_exception_fee_1": STRICT_TRANSFER_HINT_EXCEPTION_FEE_PATTERNS[0],
        "strict_transfer_exception_fee_2": STRICT_TRANSFER_HINT_EXCEPTION_FEE_PATTERNS[1],
        "transfer_hint_like_1": TRANSFER_HINT_LIKE_PATTERNS[0],
        "transfer_hint_like_2": TRANSFER_HINT_LIKE_PATTERNS[1],
        "transfer_hint_like_3": TRANSFER_HINT_LIKE_PATTERNS[2],
        "transfer_hint_like_4": TRANSFER_HINT_LIKE_PATTERNS[3],
        "transfer_hint_like_5": TRANSFER_HINT_LIKE_PATTERNS[4],
        "transfer_hint_like_6": TRANSFER_HINT_LIKE_PATTERNS[5],
        "transfer_hint_like_7": TRANSFER_HINT_LIKE_PATTERNS[6],
        "transfer_hint_like_8": TRANSFER_HINT_LIKE_PATTERNS[7],
    }
