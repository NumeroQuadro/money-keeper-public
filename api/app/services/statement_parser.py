from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional, Sequence

from ..models import Provider, TransactionDirection, TransactionMeaning
from .pdf_extract import PdfText


@dataclass(frozen=True)
class ParsedStatement:
    provider: str
    statement_type: str
    currency: str
    account_display: str
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    generated_at: Optional[datetime]
    opening_balance: Optional[Decimal]
    closing_balance: Optional[Decimal]
    total_credits: Optional[Decimal]
    total_debits: Optional[Decimal]
    reconcile_status: str
    parse_confidence: Decimal


@dataclass(frozen=True)
class ParsedRow:
    row_index: int
    page_number: int
    raw_text: str
    raw_data: dict[str, Any]
    operation_date: Optional[datetime]
    posting_date: Optional[datetime]
    amount: Optional[Decimal]
    currency: str
    direction: str
    parse_confidence: Decimal
    timestamp_precision: str = "unknown"


@dataclass(frozen=True)
class ParsedTransaction:
    amount: Decimal
    currency: str
    direction: str
    operation_datetime: Optional[datetime]
    posting_datetime: Optional[datetime]
    description_raw: str
    merchant_normalized: str
    bank_reference_id: str
    bank_category: str
    meaning: str
    meaning_confidence: Decimal
    category: str
    tags: Optional[list[str]]
    timestamp_precision: str = "unknown"


_DATE_RE = re.compile(r"(?P<d>\d{2})[./](?P<m>\d{2})[./](?P<y>\d{4})")
_MAX_ABS_MONEY = Decimal("100000000000")  # 1e11, safely under Numeric(14,2) limit
_TIME_RE = re.compile(r"\b(?P<h>\d{2}):(?P<m>\d{2})(:(?P<s>\d{2}))?\b")
_WHITESPACE_RE = re.compile(r"\s+")
_LONG_DIGITS_RE = re.compile(r"\b\d{6,}\b")
_MASKED_CARD_RE = re.compile(r"\*{2,}\d{2,4}")
_COMPANY_RE = re.compile(
    r"\b(?:ООО|АО|ПАО|ИП)\s*(?:[\"«][^\"»]{2,}[\"»]|"
    r"[A-Za-zА-Яа-я0-9.&@_/-]{2,}(?:\s+[A-Za-zА-Яа-я0-9.&@_/-]{2,}){0,3})",
    re.IGNORECASE,
)
_STAR_MERCHANT_RE = re.compile(
    r"\b[A-ZА-Я0-9._-]+\*[A-ZА-Я0-9._@-]+(?:\s+[A-ZА-Я0-9._@-]+){0,3}\b",
    re.IGNORECASE,
)
_RECIPIENT_RE = re.compile(r"(?i)\b(?:получатель|merchant|магазин)\s*:\s*(?P<merchant>[^.;,]{3,})")
_IN_MERCHANT_RE = re.compile(
    r"(?i)\bв\s+(?P<merchant>[A-Za-zА-Я0-9\"«»'._@/&-][A-Za-zА-Я0-9\"«»'._@/&\- ]{2,}?)"
    r"(?=\s+(?:дата|время|ндс|сумма|операция|$))"
)
_SENDER_RE = re.compile(r"(?i)\b(?:отправитель|sender)\s*:\s*(?P<merchant>[^.;,]{3,})")
_INCOMING_TRANSFER_RE = re.compile(
    r"(?i)\bвходящий\s+перевод(?:\s+сбп)?\s*,\s*(?P<merchant>[^,.;]{3,})"
)
_SALARY_FROM_RE = re.compile(r"(?i)\bзачисление\s+зарплаты\s+от\s+(?P<merchant>[^.;,]{3,})")
_TRANSFER_FROM_RE = re.compile(
    r"(?i)\bперевод\b[^.;]*?\bот\b\s+(?P<merchant>[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё .'-]{2,}?)"
    r"(?=\s*(?:[.,]|операция|ндс|$))"
)
_SUMMA_IZ_RE = re.compile(
    r"(?i)\bсумма\s+из\s+(?P<merchant>[A-Za-zА-Яа-я0-9*._@/&-][A-Za-zА-Яа-я0-9*._@/&\- ]{2,}?)"
    r"(?=\s+(?:дата|время|$))"
)
_LEADING_PERSON_ACCOUNT_RE = re.compile(
    r"^(?P<merchant>[А-ЯЁ][А-ЯЁ]+(?:\s+[А-ЯЁ][А-ЯЁ]+){1,3})\s+\d{10,}"
)
_CAPITALIZATION_DEPOSIT_RE = re.compile(r"(?i)^капитализация\s+вклада\b")
_INFLOW_CORRESPONDENT_ACCOUNT_RE = re.compile(r"(?i)^зачисление\s+к/с\b")
_EDS_REMAINDER_INFLOW_RE = re.compile(r"(?i)^зачисление\s+остатка\s+эдс\b")
_TRANSFER_LEADING_PREFIXES = (
    "перевод",
    "зачисление",
    "списание",
    "пополнение",
    "капитализация вклада",
)
_OZON_ALIFMOBI_INFLOW_PREFIX = (
    "Зачисление по переводу денежных средств по карте 6569 сумма из ALIFMOBI6"
)
_OZON_EDS_REMAINDER_PREFIX = "Перевод остатка ЭДС в связи повышением уровня идентификации."
_OZON_NON_TRANSFER_OUTFLOW_HINTS = (
    "{TC5}<31YY>",
    "Оплата чаевых",
    "Индивидуальный предприниматель",
    'ГУП "ПЕТЕРБУРГСКИЙ МЕТРОПОЛИТЕН"',
    'АО "ДИКСИ ЮГ"',
    'НКО "ПЕРСПЕКТИВА" (ООО)',
    'АО "ТАНДЕР"',
)
_YANDEX_WALLET_TO_BANK_TRANSFER_TEXT = "Перенос денежных средств с ЭДС на банковский счёт"


@dataclass(frozen=True)
class ParsedStatementBundle:
    meta: ParsedStatement
    rows: list[ParsedRow]
    txs: list[tuple[int, ParsedTransaction]]


@dataclass(frozen=True)
class _Record:
    page_number: int
    operation_datetime: datetime
    raw_text: str
    raw_data: dict[str, Any]
    amount: Decimal
    currency: str
    direction: str
    posting_datetime: Optional[datetime] = None
    description_raw: str = ""
    bank_reference_id: str = ""
    bank_category: str = ""
    timestamp_precision: str = "unknown"


def _detect_provider(text: PdfText, file_name: str) -> str:
    name = (file_name or "").lower()
    if "spb" in name:
        return Provider.spb.value
    if "yandex" in name or "яндекс" in name:
        return Provider.yandex.value
    if "sber" in name or "сбер" in name or "sberbank" in name:
        return Provider.sber.value
    if "ozon" in name:
        return Provider.ozon.value

    # Fall back to text-based detection using more specific hints than a bare substring.
    hay = text.full_text.lower()
    if 'банк "санкт-петербург"' in hay or "банк санкт-петербург" in hay:
        return Provider.spb.value
    if "ozon bank" in hay or "озон" in hay:
        return Provider.ozon.value
    if "sberbank" in hay or "сбербанк" in hay:
        return Provider.sber.value
    if "yandex bank" in hay or "яндекс банк" in hay:
        return Provider.yandex.value
    return Provider.unknown.value


def _detect_statement_type(file_name: str, provider_text: str) -> str:
    name = (file_name or "").lower()
    if "saving" in name or "savings" in name or "deposit" in name:
        return "savings"
    if "card" in name:
        return "card"
    if "wallet" in name:
        return "wallet"
    if "счет" in provider_text.lower() or "счёт" in provider_text.lower():
        return "payment"
    return "unknown"


def _parse_date(value: str) -> Optional[datetime]:
    m = _DATE_RE.search(value)
    if not m:
        return None
    try:
        return datetime(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    except ValueError:
        return None


def _parse_money(value: str) -> Optional[Decimal]:
    v = value.strip()
    if not v:
        return None
    v = v.replace("−", "-").replace("–", "-")
    # Normalize common statement formats: "1 234,56" / "-1 234.56" / "1234.56"
    v = v.replace("\u00a0", " ").replace(" ", "")
    v = v.replace(",", ".")
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _extract_candidate_amounts(line: str) -> list[Decimal]:
    candidates: list[Decimal] = []
    for parsed, _ in _extract_candidate_amount_tokens(line):
        candidates.append(parsed)
    return candidates


def _extract_candidate_amount_tokens(line: str) -> list[tuple[Decimal, bool]]:
    """Return parsed money candidates and whether each token had an explicit sign."""
    candidates: list[tuple[Decimal, bool]] = []
    for m in re.finditer(r"(?P<sign>[+\-–−])?\s*(?P<num>\d[\d\s\u00a0]*[.,]\d{2})", line):
        raw = (m.group("sign") or "") + (m.group("num") or "")
        parsed = _parse_money(raw)
        if parsed is not None and abs(parsed) <= _MAX_ABS_MONEY:
            candidates.append((parsed, bool(m.group("sign"))))
    return candidates


def _extract_signed_ruble_amounts(line: str) -> list[Decimal]:
    """Extract signed money tokens that explicitly end with the RUB symbol."""
    candidates: list[Decimal] = []
    pattern = r"(?P<sign>[+\-–−])\s*(?P<num>\d[\d\s\u00a0]*[.,]\d{2})\s*₽"
    for m in re.finditer(pattern, line):
        raw = (m.group("sign") or "") + (m.group("num") or "")
        parsed = _parse_money(raw)
        if parsed is not None and abs(parsed) <= _MAX_ABS_MONEY:
            candidates.append(parsed)
    return candidates


def _parse_time(value: str) -> Optional[tuple[int, int, int]]:
    match = _TIME_RE.search(value)
    if not match:
        return None
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s") or "0")
    if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
        return None
    return hours, minutes, seconds


def _combine_date_time(date_value: str, time_value: str) -> Optional[datetime]:
    date = _parse_date(date_value)
    if date is None:
        return None
    parsed_time = _parse_time(time_value)
    if parsed_time is None:
        return date
    hours, minutes, seconds = parsed_time
    return date.replace(hour=hours, minute=minutes, second=seconds)


def _normalize_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value or "").strip()


def _clean_merchant_candidate(value: str) -> str:
    cleaned = _normalize_whitespace(value)
    if not cleaned:
        return ""
    cleaned = _LONG_DIGITS_RE.sub(" ", cleaned)
    cleaned = _MASKED_CARD_RE.sub(" ", cleaned)
    cleaned = _normalize_whitespace(cleaned).strip(" -:;,.")
    cleaned = cleaned.replace('"', "").replace("«", "").replace("»", "")
    cleaned = _normalize_whitespace(cleaned)
    if len(cleaned) < 2:
        return ""
    return cleaned[:120]


def _has_letters(value: str) -> bool:
    return bool(re.search(r"[A-Za-zА-Яа-яЁё]", value or ""))


def _extract_inflow_counterparty(text: str) -> str:
    for pattern in (
        _SENDER_RE,
        _INCOMING_TRANSFER_RE,
        _SALARY_FROM_RE,
        _TRANSFER_FROM_RE,
        _SUMMA_IZ_RE,
        _LEADING_PERSON_ACCOUNT_RE,
    ):
        match = pattern.search(text)
        if not match:
            continue
        candidate = match.group("merchant")
        if not _has_letters(candidate):
            continue
        merchant = _clean_merchant_candidate(candidate)
        if merchant and _has_letters(merchant):
            return merchant

    if _CAPITALIZATION_DEPOSIT_RE.search(text):
        return "Капитализация вклада"

    if _INFLOW_CORRESPONDENT_ACCOUNT_RE.search(text):
        return "Зачисление к/с"

    if _EDS_REMAINDER_INFLOW_RE.search(text):
        return "Остаток ЭДС"

    return ""


def _normalize_ozon_description_for_cashflow(*, description_raw: str, direction: str) -> str:
    text = _normalize_whitespace(description_raw)
    if not text:
        return ""

    if text.startswith(_OZON_ALIFMOBI_INFLOW_PREFIX):
        return text.replace("по переводу денежных средств", "по зачислению денежных средств", 1)

    if text.startswith(_OZON_EDS_REMAINDER_PREFIX):
        return re.sub(r"^Перевод", "Зачисление", text, count=1)

    if direction != TransactionDirection.outflow.value or not text.startswith("Перевод "):
        return text

    if not any(hint in text for hint in _OZON_NON_TRANSFER_OUTFLOW_HINTS):
        return text

    normalized = text.replace("Перевод ", "Оплата ", 1)
    normalized = re.sub(r"(?i)\s*через\s+СБП\.\s*", " ", normalized)
    return _normalize_whitespace(normalized)


def _normalize_yandex_description_for_cashflow(*, description_raw: str, direction: str) -> str:
    text = _normalize_whitespace(description_raw)
    if direction == TransactionDirection.outflow.value:
        text = re.sub(r"(?i)^Оплата\s+СБП\s+QR", "Оплата QR", text, count=1)
    return text


def _normalize_sber_description_for_cashflow(*, description_raw: str, direction: str) -> str:
    text = _normalize_whitespace(description_raw)
    if direction == TransactionDirection.inflow.value:
        # Merchant QR refunds are non-transfer inflows for strict cashflow parity.
        text = re.sub(
            r"(?i)(возврат\s+покупки\s+по\s+qr[–-]?коду)\s+сбп\b",
            r"\1",
            text,
            count=1,
        )
    return _normalize_whitespace(text)


def _infer_yandex_bank_category(*, description_raw: str) -> str:
    if _normalize_whitespace(description_raw) == _YANDEX_WALLET_TO_BANK_TRANSFER_TEXT:
        return "transfer"
    return ""


def _extract_merchant_label(
    *, description_raw: str, bank_category: str, direction: str = ""
) -> str:
    text = _normalize_whitespace(description_raw)
    if not text:
        return ""

    if direction == TransactionDirection.inflow.value:
        inflow_counterparty = _extract_inflow_counterparty(text)
        if inflow_counterparty:
            return inflow_counterparty

    lower = text.lower()
    if lower.startswith(_TRANSFER_LEADING_PREFIXES):
        return ""

    for pattern in (_RECIPIENT_RE, _IN_MERCHANT_RE):
        match = pattern.search(text)
        if match:
            merchant = _clean_merchant_candidate(match.group("merchant"))
            if merchant:
                return merchant

    for pattern in (_STAR_MERCHANT_RE, _COMPANY_RE):
        match = pattern.search(text)
        if match:
            merchant = _clean_merchant_candidate(match.group(0))
            if merchant:
                return merchant

    stripped = _normalize_whitespace(_LONG_DIGITS_RE.sub(" ", _MASKED_CARD_RE.sub(" ", text)))
    stripped = re.split(
        r"(?i)\b(?:ндс|операция по|дата|время|интернет-банк|договор|документ)\b",
        stripped,
        maxsplit=1,
    )[0]
    stripped = re.split(r"[;|]", stripped, maxsplit=1)[0]
    stripped = re.sub(
        r"(?i)^(?:оплата товаров по карте|оплата по qr-коду|оплата|покупка)\s+",
        "",
        stripped,
    )
    merchant = _clean_merchant_candidate(stripped)
    if merchant:
        return merchant
    return _clean_merchant_candidate(bank_category)


def _iter_page_lines(pages: Sequence[tuple[int, str]]) -> Iterable[tuple[int, str]]:
    for page_number, page_text in pages:
        for raw_line in (page_text or "").splitlines():
            line = (raw_line or "").strip()
            if not line:
                continue
            yield page_number, line


def _reconcile_status(
    *,
    opening_balance: Optional[Decimal],
    closing_balance: Optional[Decimal],
    total_credits: Optional[Decimal],
    total_debits: Optional[Decimal],
    txs: Sequence[_Record],
) -> str:
    if opening_balance is None or closing_balance is None:
        return "unknown"

    if total_credits is not None and total_debits is not None:
        expected = opening_balance + total_credits - total_debits
    else:
        credits = sum(
            (r.amount for r in txs if r.direction == TransactionDirection.inflow.value),
            Decimal("0"),
        )
        debits = sum(
            (r.amount for r in txs if r.direction == TransactionDirection.outflow.value),
            Decimal("0"),
        )
        expected = opening_balance + credits - debits

    if abs(expected - closing_balance) <= Decimal("0.01"):
        return "ok"
    return "mismatch"


def parse_statement_meta(*, pdf_text: PdfText, file_name: str, pdf_path: str) -> ParsedStatement:
    provider = _detect_provider(pdf_text, file_name)
    statement_type = _detect_statement_type(file_name, pdf_text.full_text)

    # MVP: we don't reliably extract period/balances for all providers yet.
    return ParsedStatement(
        provider=provider,
        statement_type=statement_type,
        currency="RUB",
        account_display=file_name or pdf_path,
        period_start=None,
        period_end=None,
        generated_at=None,
        opening_balance=None,
        closing_balance=None,
        total_credits=None,
        total_debits=None,
        reconcile_status="unknown",
        parse_confidence=Decimal("0.20"),
    )


def parse_rows_and_transactions(
    *, pdf_text: PdfText
) -> tuple[list[ParsedRow], list[tuple[int, ParsedTransaction]]]:
    """
    Returns:
    - Parsed rows (one per detected transaction-like line)
    - Transactions paired with the originating row_index for linking

    Notes:
    - This is a best-effort parser intended for MVP scaffolding.
    - It only attempts to parse lines that start with a date and contain at least one money value.
    """
    rows: list[ParsedRow] = []
    txs: list[tuple[int, ParsedTransaction]] = []

    row_index = 0
    for page_number, page_text in enumerate(pdf_text.pages, start=1):
        for raw_line in (page_text or "").splitlines():
            line = (raw_line or "").strip()
            if not line:
                continue

            date_match = _DATE_RE.search(line)
            if not date_match:
                continue
            op_date = _parse_date(line)
            if not op_date:
                continue

            rest = line[date_match.end() :].strip()
            # Some statement formats include multiple dates (operation/posting) on the same line.
            # Remove any remaining dates from the searchable area so we don't treat "DD.MM" as money.
            search_area = _DATE_RE.sub(" ", rest)
            amounts = _extract_candidate_amounts(search_area)
            if not amounts:
                continue

            # Heuristic: prefer the first amount to avoid accidentally picking an end-of-line balance column.
            amount = amounts[0]
            direction = (
                TransactionDirection.inflow.value
                if amount > 0
                else TransactionDirection.outflow.value
            )
            amount_abs = abs(amount)

            parsed_row = ParsedRow(
                row_index=row_index,
                page_number=page_number,
                raw_text=line,
                raw_data={"amount_candidates": [str(a) for a in amounts]},
                operation_date=op_date,
                posting_date=None,
                amount=amount_abs,
                currency="RUB",
                direction=direction,
                parse_confidence=Decimal("0.30"),
                timestamp_precision="date_only",
            )
            rows.append(parsed_row)

            # Description: everything after the date token.
            desc = rest

            tx = ParsedTransaction(
                amount=amount_abs,
                currency="RUB",
                direction=direction,
                operation_datetime=op_date,
                posting_datetime=None,
                description_raw=desc,
                merchant_normalized=_extract_merchant_label(
                    description_raw=desc,
                    bank_category="",
                    direction=direction,
                ),
                bank_reference_id="",
                bank_category="",
                meaning=TransactionMeaning.unknown.value,
                meaning_confidence=Decimal("0.00"),
                category="",
                tags=None,
                timestamp_precision="date_only",
            )
            txs.append((row_index, tx))
            row_index += 1

    return rows, txs


def parse_pdf_into_statements(
    *, pdf_text: PdfText, file_name: str, pdf_path: str
) -> list[ParsedStatementBundle]:
    provider = _detect_provider(pdf_text, file_name)
    if provider == Provider.spb.value:
        return _parse_spb(pdf_text=pdf_text, file_name=file_name, pdf_path=pdf_path)
    if provider == Provider.ozon.value:
        return _parse_ozon(pdf_text=pdf_text, file_name=file_name, pdf_path=pdf_path)
    if provider == Provider.yandex.value:
        return _parse_yandex(pdf_text=pdf_text, file_name=file_name, pdf_path=pdf_path)
    if provider == Provider.sber.value:
        return _parse_sber(pdf_text=pdf_text, file_name=file_name, pdf_path=pdf_path)

    meta = parse_statement_meta(pdf_text=pdf_text, file_name=file_name, pdf_path=pdf_path)
    rows, txs = parse_rows_and_transactions(pdf_text=pdf_text)
    return [ParsedStatementBundle(meta=meta, rows=rows, txs=txs)]


def _parse_ozon(*, pdf_text: PdfText, file_name: str, pdf_path: str) -> list[ParsedStatementBundle]:
    # Ozon PDFs may include multiple statement sections stitched together.
    starts: list[int] = []
    for i, page in enumerate(pdf_text.pages, start=1):
        if "Справка о движении средств" in (page or ""):
            starts.append(i)
    if not starts:
        starts = [1]

    bundles: list[ParsedStatementBundle] = []
    for idx, start_page in enumerate(starts):
        end_page = (starts[idx + 1] - 1) if idx + 1 < len(starts) else len(pdf_text.pages)
        page_pairs = [(pno, pdf_text.pages[pno - 1]) for pno in range(start_page, end_page + 1)]
        page_text = "\n".join(text for _, text in page_pairs)

        period = re.search(
            r"Период выписки:\s*(\d{2}\.\d{2}\.\d{4})\s*[–-]\s*(\d{2}\.\d{2}\.\d{4})", page_text
        )
        period_start = _parse_date(period.group(1)) if period else None
        period_end = _parse_date(period.group(2)) if period else None

        generated = re.search(
            r"Дата и время формирования документа:\s*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2}:\d{2})",
            page_text,
        )
        generated_at = (
            _combine_date_time(generated.group(1), generated.group(2))
            if generated is not None
            else None
        )

        account_match = re.search(r"Номер лицевого сч[её]та:\s*№\s*([0-9 ]+)", page_text)
        account_number = (account_match.group(1) if account_match else "").replace(" ", "")

        opening_match = re.search(r"Входящий остаток:\s*([0-9\s\u00a0.,]+)\s*₽", page_text)
        opening_balance = _parse_money(opening_match.group(1)) if opening_match else None

        closing_match = re.search(r"Исходящий остаток:\s*([0-9\s\u00a0.,]+)\s*₽", page_text)
        closing_balance = _parse_money(closing_match.group(1)) if closing_match else None

        credits_match = re.search(
            r"Итого зачислений за период:\s*([0-9\s\u00a0.,]+)\s*₽", page_text
        )
        total_credits = _parse_money(credits_match.group(1)) if credits_match else None

        debits_match = re.search(r"Итого списаний за период:\s*([0-9\s\u00a0.,]+)\s*₽", page_text)
        total_debits = _parse_money(debits_match.group(1)) if debits_match else None

        records = _parse_ozon_records(page_pairs)
        reconcile_status = _reconcile_status(
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_credits=total_credits,
            total_debits=total_debits,
            txs=records,
        )

        meta = ParsedStatement(
            provider=Provider.ozon.value,
            statement_type=_detect_statement_type(file_name, page_text),
            currency="RUB",
            account_display=account_number or (file_name or pdf_path),
            period_start=period_start,
            period_end=period_end,
            generated_at=generated_at,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_credits=total_credits,
            total_debits=total_debits,
            reconcile_status=reconcile_status,
            parse_confidence=Decimal("0.85") if period_start and period_end else Decimal("0.60"),
        )

        rows: list[ParsedRow] = []
        txs: list[tuple[int, ParsedTransaction]] = []
        for row_index, rec in enumerate(records):
            parsed_row = ParsedRow(
                row_index=row_index,
                page_number=rec.page_number,
                raw_text=rec.raw_text,
                raw_data=rec.raw_data,
                operation_date=rec.operation_datetime,
                posting_date=rec.posting_datetime,
                amount=rec.amount,
                currency=rec.currency,
                direction=rec.direction,
                parse_confidence=Decimal("0.80"),
                timestamp_precision=rec.timestamp_precision,
            )
            rows.append(parsed_row)

            tx = ParsedTransaction(
                amount=rec.amount,
                currency=rec.currency,
                direction=rec.direction,
                operation_datetime=rec.operation_datetime,
                posting_datetime=rec.posting_datetime,
                description_raw=rec.description_raw,
                merchant_normalized=_extract_merchant_label(
                    description_raw=rec.description_raw,
                    bank_category=rec.bank_category,
                    direction=rec.direction,
                ),
                bank_reference_id=rec.bank_reference_id,
                bank_category=rec.bank_category,
                meaning=TransactionMeaning.unknown.value,
                meaning_confidence=Decimal("0.00"),
                category="",
                tags=None,
                timestamp_precision=rec.timestamp_precision,
            )
            txs.append((row_index, tx))

        bundles.append(ParsedStatementBundle(meta=meta, rows=rows, txs=txs))

    return bundles


def _parse_ozon_records(pages: Sequence[tuple[int, str]]) -> list[_Record]:
    records: list[_Record] = []
    current: Optional[dict[str, Any]] = None
    pending_date: Optional[str] = None

    date_only = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
    full_start = re.compile(
        r"^(?P<date>\d{2}\.\d{2}\.\d{4})\s+(?P<time>\d{2}:\d{2}(:\d{2})?)\s+(?P<doc>\d+)\s*(?P<rest>.*)$"
    )
    time_doc = re.compile(r"^(?P<time>\d{2}:\d{2}(:\d{2})?)\s+(?P<doc>\d+)\s*(?P<rest>.*)$")

    def flush() -> None:
        nonlocal current
        if not current:
            return
        raw_lines: list[str] = current["raw_lines"]
        joined = " ".join(raw_lines).strip()

        money_area = _DATE_RE.sub(" ", joined)
        amounts = _extract_candidate_amounts(money_area)
        if not amounts:
            current = None
            return

        # Ozon rows often contain auxiliary unsigned numbers in free text
        # (order IDs, payload fragments). Prefer explicit signed RUB tokens.
        signed_ruble = _extract_signed_ruble_amounts(money_area)
        signed = signed_ruble[0] if signed_ruble else amounts[0]
        direction = (
            TransactionDirection.inflow.value if signed > 0 else TransactionDirection.outflow.value
        )
        amount_abs = abs(signed)

        desc_lines: list[str] = [ln for ln in current.get("desc_lines", []) if ln]
        desc_joined = " ".join(desc_lines).strip()
        desc_joined = re.sub(r"[+\-–−]?\s*\d[\d\s\u00a0]*[.,]\d{2}\s*₽?", " ", desc_joined)
        desc_joined = re.sub(r"\s+", " ", desc_joined).strip()
        desc_joined = _normalize_ozon_description_for_cashflow(
            description_raw=desc_joined,
            direction=direction,
        )

        operation_datetime: datetime = current["operation_datetime"]
        currency = "RUB"
        raw_data = {"provider": Provider.ozon.value, "doc_id": current.get("doc_id", "")}

        records.append(
            _Record(
                page_number=current["page_number"],
                operation_datetime=operation_datetime,
                raw_text=joined,
                raw_data=raw_data,
                amount=amount_abs,
                currency=currency,
                direction=direction,
                description_raw=desc_joined,
                bank_reference_id=current.get("doc_id", ""),
                timestamp_precision=current.get("timestamp_precision", "exact"),
            )
        )
        current = None

    for page_number, line in _iter_page_lines(pages):
        if date_only.match(line):
            pending_date = line
            continue

        start_match = full_start.match(line)
        if start_match:
            flush()
            doc_id = start_match.group("doc") or ""
            dt = _combine_date_time(start_match.group("date"), start_match.group("time"))
            if dt is None:
                continue
            rest = (start_match.group("rest") or "").strip()
            current = {
                "page_number": page_number,
                "operation_datetime": dt,
                "doc_id": doc_id,
                "raw_lines": [line],
                "desc_lines": [rest] if rest else [],
                "timestamp_precision": "exact",
            }
            pending_date = None
            continue

        if pending_date:
            time_match = time_doc.match(line)
            if time_match:
                flush()
                doc_id = time_match.group("doc") or ""
                dt = _combine_date_time(pending_date, time_match.group("time"))
                if dt is None:
                    pending_date = None
                    continue
                rest = (time_match.group("rest") or "").strip()
                current = {
                    "page_number": page_number,
                    "operation_datetime": dt,
                    "doc_id": doc_id,
                    "raw_lines": [pending_date, line],
                    "desc_lines": [rest] if rest else [],
                    "timestamp_precision": "exact",
                }
                pending_date = None
                continue

        if current is not None:
            current["raw_lines"].append(line)
            current.setdefault("desc_lines", []).append(line)

    flush()
    return records


def _parse_yandex(
    *, pdf_text: PdfText, file_name: str, pdf_path: str
) -> list[ParsedStatementBundle]:
    starts: list[int] = []
    for i, page in enumerate(pdf_text.pages, start=1):
        if "Выписка по договору" in (page or "") and "Исх. №" in (page or ""):
            starts.append(i)
    if not starts:
        starts = [1]

    bundles: list[ParsedStatementBundle] = []
    for idx, start_page in enumerate(starts):
        end_page = (starts[idx + 1] - 1) if idx + 1 < len(starts) else len(pdf_text.pages)
        page_pairs = [(pno, pdf_text.pages[pno - 1]) for pno in range(start_page, end_page + 1)]
        page_text = "\n".join(text for _, text in page_pairs)

        period = re.search(
            r"Выписка по Договору за период с (\d{2}\.\d{2}\.\d{4}) по (\d{2}\.\d{2}\.\d{4})",
            page_text,
        )
        period_start = _parse_date(period.group(1)) if period else None
        period_end = _parse_date(period.group(2)) if period else None

        generated = re.search(r"\bДата\s+(\d{2}\.\d{2}\.\d{4})\b", page_text)
        generated_at = _parse_date(generated.group(1)) if generated else None

        account_match = re.search(r"В рамках Договора открыт сч[её]т\s+(\d+)", page_text)
        account_number = account_match.group(1) if account_match else ""

        opening_match = re.search(
            r"Входящий остаток на\s*(\d{2}\.\d{2}\.\d{4})\s*([0-9\s\u00a0.,]+)\s*₽", page_text
        )
        opening_balance = _parse_money(opening_match.group(2)) if opening_match else None

        closing_match = re.search(
            r"Исходящий остаток за\s*(\d{2}\.\d{2}\.\d{4})\s*([0-9\s\u00a0.,]+)\s*₽", page_text
        )
        closing_balance = _parse_money(closing_match.group(2)) if closing_match else None

        credits_match = re.search(r"Итого зачислений\s*([0-9\s\u00a0.,]+)\s*₽", page_text)
        total_credits = _parse_money(credits_match.group(1)) if credits_match else None

        debits_match = re.search(r"Итого списаний\s*([0-9\s\u00a0.,]+)\s*₽", page_text)
        total_debits = _parse_money(debits_match.group(1)) if debits_match else None

        records = _parse_yandex_records(page_pairs)
        reconcile_status = _reconcile_status(
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_credits=total_credits,
            total_debits=total_debits,
            txs=records,
        )

        meta = ParsedStatement(
            provider=Provider.yandex.value,
            statement_type=_detect_statement_type(file_name, page_text),
            currency="RUB",
            account_display=account_number or (file_name or pdf_path),
            period_start=period_start,
            period_end=period_end,
            generated_at=generated_at,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_credits=total_credits,
            total_debits=total_debits,
            reconcile_status=reconcile_status,
            parse_confidence=Decimal("0.85") if period_start and period_end else Decimal("0.60"),
        )

        rows: list[ParsedRow] = []
        txs: list[tuple[int, ParsedTransaction]] = []
        for row_index, rec in enumerate(records):
            parsed_row = ParsedRow(
                row_index=row_index,
                page_number=rec.page_number,
                raw_text=rec.raw_text,
                raw_data=rec.raw_data,
                operation_date=rec.operation_datetime,
                posting_date=rec.posting_datetime,
                amount=rec.amount,
                currency=rec.currency,
                direction=rec.direction,
                parse_confidence=Decimal("0.80"),
                timestamp_precision=rec.timestamp_precision,
            )
            rows.append(parsed_row)

            tx = ParsedTransaction(
                amount=rec.amount,
                currency=rec.currency,
                direction=rec.direction,
                operation_datetime=rec.operation_datetime,
                posting_datetime=rec.posting_datetime,
                description_raw=rec.description_raw,
                merchant_normalized=_extract_merchant_label(
                    description_raw=rec.description_raw,
                    bank_category=rec.bank_category,
                    direction=rec.direction,
                ),
                bank_reference_id=rec.bank_reference_id,
                bank_category=rec.bank_category,
                meaning=TransactionMeaning.unknown.value,
                meaning_confidence=Decimal("0.00"),
                category="",
                tags=None,
                timestamp_precision=rec.timestamp_precision,
            )
            txs.append((row_index, tx))

        bundles.append(ParsedStatementBundle(meta=meta, rows=rows, txs=txs))

    return bundles


def _parse_yandex_records(pages: Sequence[tuple[int, str]]) -> list[_Record]:
    records: list[_Record] = []
    desc_buffer: list[str] = []
    recent: deque[str] = deque(maxlen=5)
    pending_operation_date: Optional[str] = None
    pending_operation_time: Optional[str] = None

    def is_header(line: str) -> bool:
        lower = line.lower()
        if lower.startswith("страница"):
            return True
        if lower in {"операции", "мск", "дата", "обработки", "карта", "эсп", "договора"}:
            return True
        if lower.startswith("сумма в валюте"):
            return True
        return any(
            key in lower
            for key in [
                "выписка по договору",
                "файл содержит",
                "описание операции",
                "дата и время",
                "дата операции",
                "дата обработки",
                "карта сумма",
                "сумма в валюте",
                "входящий остаток",
                "исходящий остаток",
                "итого ",
                "всего ",
            ]
        )

    def take_time() -> Optional[str]:
        for candidate in reversed(recent):
            m = re.search(r"в\s*(\d{2}:\d{2}(:\d{2})?)", candidate)
            if m:
                return m.group(1)
        return None

    for page_number, line in _iter_page_lines(pages):
        recent.append(line)

        if is_header(line):
            desc_buffer.clear()
            pending_operation_date = None
            pending_operation_time = None
            continue

        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", line):
            # Date line is operation date for the next row and can differ from inline processing date.
            pending_operation_date = line
            pending_operation_time = None
            continue
        time_line = re.match(r"^в\s*(\d{2}:\d{2}(:\d{2})?)", line)
        if time_line:
            pending_operation_time = time_line.group(1)
            continue

        date_match = _DATE_RE.search(line)
        if date_match and "₽" in line and "остаток" not in line.lower():
            search_area = _DATE_RE.sub(" ", line)
            amounts = _extract_candidate_amounts(search_area)
            if amounts:
                signed = amounts[0]
                direction = (
                    TransactionDirection.inflow.value
                    if signed > 0
                    else TransactionDirection.outflow.value
                )
                amount_abs = abs(signed)

                inline_date_str = date_match.group(0)
                operation_date_str = pending_operation_date or inline_date_str
                time_str = pending_operation_time or take_time()
                operation_dt = (
                    _combine_date_time(operation_date_str, time_str)
                    if time_str
                    else _parse_date(operation_date_str)
                )
                if operation_dt is None:
                    desc_buffer.clear()
                    pending_operation_date = None
                    pending_operation_time = None
                    continue

                inline_desc = line[: date_match.start()].strip()
                description = " ".join([*desc_buffer, inline_desc]).strip()
                description = re.sub(r"\s+", " ", description)
                description = _normalize_yandex_description_for_cashflow(
                    description_raw=description,
                    direction=direction,
                )
                bank_category = _infer_yandex_bank_category(description_raw=description)
                desc_buffer.clear()
                pending_operation_date = None
                pending_operation_time = None

                records.append(
                    _Record(
                        page_number=page_number,
                        operation_datetime=operation_dt,
                        raw_text=line,
                        raw_data={"provider": Provider.yandex.value},
                        amount=amount_abs,
                        currency="RUB",
                        direction=direction,
                        description_raw=description,
                        bank_category=bank_category,
                        timestamp_precision="exact" if time_str else "date_only",
                    )
                )
                continue

        # Default: treat as description line.
        desc_buffer.append(line)

    return records


def _parse_spb(*, pdf_text: PdfText, file_name: str, pdf_path: str) -> list[ParsedStatementBundle]:
    pages = [(i, page) for i, page in enumerate(pdf_text.pages, start=1)]
    page_text = pdf_text.full_text

    period = re.search(
        r"Период:\s*(\d{2}\.\d{2}\.\d{4})\s*[—-]\s*(\d{2}\.\d{2}\.\d{4})",
        page_text,
        re.IGNORECASE,
    )
    period_start = _parse_date(period.group(1)) if period else None
    period_end = _parse_date(period.group(2)) if period else None

    account_match = re.search(r"Сч[её]т:\s*([0-9 ]{10,})", page_text, re.IGNORECASE)
    account_display = account_match.group(1).strip() if account_match else (file_name or pdf_path)

    opening_balance = None
    opening_match = re.search(
        r"([+\-–−]?\s*\d[\d \u00a0]*[.,]\d{2})\s*Входящий остаток на\s*\d{2}\.\d{2}\.\d{4}",
        page_text,
        re.IGNORECASE,
    )
    if opening_match:
        opening_balance = _parse_money(opening_match.group(1))

    closing_balance = None
    total_credits = None
    total_debits = None
    totals_match = re.search(
        r"([+\-–−]?\s*\d[\d \u00a0]*[.,]\d{2})\s+([+\-–−]?\s*\d[\d \u00a0]*[.,]\d{2})\s+([+\-–−]?\s*\d[\d \u00a0]*[.,]\d{2})\s+Поступление\s+Списание\s+Исходящий остаток\s+на\s+\d{2}\.\d{2}\.\d{4}",
        page_text,
        re.IGNORECASE | re.DOTALL,
    )
    if totals_match:
        closing_balance = _parse_money(totals_match.group(1))
        parsed_credits = _parse_money(totals_match.group(2))
        parsed_debits = _parse_money(totals_match.group(3))
        total_credits = abs(parsed_credits) if parsed_credits is not None else None
        total_debits = abs(parsed_debits) if parsed_debits is not None else None

    records = _parse_spb_records(pages)
    reconcile_status = _reconcile_status(
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        txs=records,
    )

    meta = ParsedStatement(
        provider=Provider.spb.value,
        statement_type=_detect_statement_type(file_name, page_text),
        currency="RUB",
        account_display=account_display,
        period_start=period_start,
        period_end=period_end,
        generated_at=None,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        reconcile_status=reconcile_status,
        parse_confidence=Decimal("0.75") if period_start and period_end else Decimal("0.55"),
    )

    rows: list[ParsedRow] = []
    txs: list[tuple[int, ParsedTransaction]] = []
    for row_index, rec in enumerate(records):
        parsed_row = ParsedRow(
            row_index=row_index,
            page_number=rec.page_number,
            raw_text=rec.raw_text,
            raw_data=rec.raw_data,
            operation_date=rec.operation_datetime,
            posting_date=rec.posting_datetime,
            amount=rec.amount,
            currency=rec.currency,
            direction=rec.direction,
            parse_confidence=Decimal("0.70"),
            timestamp_precision=rec.timestamp_precision,
        )
        rows.append(parsed_row)

        tx = ParsedTransaction(
            amount=rec.amount,
            currency=rec.currency,
            direction=rec.direction,
            operation_datetime=rec.operation_datetime,
            posting_datetime=rec.posting_datetime,
            description_raw=rec.description_raw,
            merchant_normalized=_extract_merchant_label(
                description_raw=rec.description_raw,
                bank_category=rec.bank_category,
                direction=rec.direction,
            ),
            bank_reference_id=rec.bank_reference_id,
            bank_category=rec.bank_category,
            meaning=TransactionMeaning.unknown.value,
            meaning_confidence=Decimal("0.00"),
            category="",
            tags=None,
            timestamp_precision=rec.timestamp_precision,
        )
        txs.append((row_index, tx))

    return [ParsedStatementBundle(meta=meta, rows=rows, txs=txs)]


def _parse_spb_records(pages: Sequence[tuple[int, str]]) -> list[_Record]:
    current: Optional[dict[str, Any]] = None
    records: list[_Record] = []

    record_start = re.compile(r"^(?P<date>\d{2}\.\d{2}\.\d{4})\s+(?P<rest>.+)$")
    money_only = re.compile(r"^[+\-–−]?\s*\d[\d\s\u00a0]*[.,]\d{2}$")
    page_marker = re.compile(r"^/\d+\s+\d+$")
    generated_line = re.compile(r"^\d{2}\.\d{2}\.\d{4}\s*\|\s*\d{2}:\d{2}$")

    def is_noise_line(line: str) -> bool:
        lower = line.lower()
        if lower.startswith("период:") or lower == "выписка":
            return True
        if lower.startswith("дата плательщик"):
            return True
        if "входящий остаток" in lower:
            return True
        if lower in {"поступление", "списание"}:
            return True
        if lower.startswith("исходящий остаток"):
            return True
        if lower.startswith("иванов иван"):
            return True
        if lower.startswith("бик "):
            return True
        if page_marker.fullmatch(line):
            return True
        if generated_line.fullmatch(line):
            return True
        return False

    def infer_bank_category(text: str) -> str:
        lower = text.lower()
        if "перевод" in lower or "сбп" in lower or "sbp" in lower:
            return "transfer"
        if "зарплат" in lower:
            return "salary"
        if "кешбэк" in lower or "поощрен" in lower:
            return "cashback"
        return ""

    def infer_hidden_operation_datetime(
        *, operation_date: datetime, raw_lines: Sequence[str]
    ) -> tuple[datetime, dict[str, str]] | None:
        date_token = operation_date.strftime("%d.%m.%Y")
        same_date_time = re.compile(
            rf"(?:^|\s){re.escape(date_token)}\s+(?P<time>\d{{2}}:\d{{2}}(?::\d{{2}})?)\b"
        )
        for raw_line in raw_lines:
            match = same_date_time.search(raw_line)
            if not match:
                continue
            inferred = _combine_date_time(date_token, match.group("time"))
            if inferred is None:
                continue
            return (
                inferred,
                {
                    "method": "spb_same_date_inline_time",
                    "matched_time": match.group("time"),
                    "source_line": _normalize_whitespace(raw_line)[:240],
                },
            )
        return None

    def flush() -> None:
        nonlocal current
        if not current:
            return

        raw_lines: list[str] = current["raw_lines"]
        joined = " ".join(raw_lines).strip()
        amounts = _extract_candidate_amounts(_DATE_RE.sub(" ", joined))
        if not amounts:
            current = None
            return

        signed = amounts[0]
        direction = (
            TransactionDirection.inflow.value if signed > 0 else TransactionDirection.outflow.value
        )
        if signed == 0:
            direction = TransactionDirection.outflow.value
        amount_abs = abs(signed)

        description_parts = [line for line in current.get("desc_lines", []) if line]
        description = " ".join(description_parts).strip()
        description = re.sub(r"[+\-–−]?\s*\d[\d\s\u00a0]*[.,]\d{2}", " ", description)
        description = re.sub(r"\s+", " ", description).strip()

        bank_ref_match = re.search(r"код\s+операции\s+([A-Za-z0-9]+)", joined, re.IGNORECASE)
        bank_ref = bank_ref_match.group(1) if bank_ref_match else ""

        operation_datetime = current["operation_datetime"]
        timestamp_precision = current.get("timestamp_precision", "date_only")
        timestamp_evidence = current.get("timestamp_evidence")
        if timestamp_precision == "date_only":
            inferred = infer_hidden_operation_datetime(
                operation_date=operation_datetime,
                raw_lines=raw_lines,
            )
            if inferred is not None:
                operation_datetime, timestamp_evidence = inferred
                timestamp_precision = "inferred"

        raw_data: dict[str, Any] = {
            "provider": Provider.spb.value,
            "timestamp_precision": timestamp_precision,
        }
        if timestamp_evidence:
            raw_data["timestamp_evidence"] = timestamp_evidence
        if bank_ref:
            raw_data["bank_reference_id"] = bank_ref

        records.append(
            _Record(
                page_number=current["page_number"],
                operation_datetime=operation_datetime,
                posting_datetime=None,
                raw_text=joined,
                raw_data=raw_data,
                amount=amount_abs,
                currency="RUB",
                direction=direction,
                description_raw=description or joined,
                bank_reference_id=bank_ref,
                bank_category=infer_bank_category(description),
                timestamp_precision=timestamp_precision,
            )
        )
        current = None

    for page_number, line in _iter_page_lines(pages):
        if is_noise_line(line):
            continue

        start_match = record_start.match(line)
        if start_match:
            flush()
            operation_dt = _parse_date(start_match.group("date"))
            if operation_dt is None:
                continue
            rest = (start_match.group("rest") or "").strip()
            timestamp_precision = "date_only"
            timestamp_evidence: dict[str, str] | None = None
            inline_time_match = _TIME_RE.search(rest)
            if inline_time_match:
                inferred_dt = _combine_date_time(
                    start_match.group("date"), inline_time_match.group(0)
                )
                if inferred_dt is not None:
                    operation_dt = inferred_dt
                    timestamp_precision = "inferred"
                    timestamp_evidence = {
                        "method": "spb_start_line_time",
                        "matched_time": inline_time_match.group(0),
                        "source_line": _normalize_whitespace(line)[:240],
                    }
            current = {
                "page_number": page_number,
                "operation_datetime": operation_dt,
                "raw_lines": [line],
                "desc_lines": [rest] if rest else [],
                "timestamp_precision": timestamp_precision,
                "timestamp_evidence": timestamp_evidence,
            }
            continue

        if current is None:
            continue

        current["raw_lines"].append(line)
        if not money_only.fullmatch(line):
            current["desc_lines"].append(line)

    flush()

    # Preserve repeated same-statement operations as distinct canonicals by assigning
    # deterministic synthetic references to duplicate rows that lack bank references.
    duplicate_group_sizes: dict[tuple[str, str, str, str, str], int] = {}
    for rec in records:
        if rec.bank_reference_id:
            continue
        key = (
            rec.operation_datetime.replace(microsecond=0).isoformat(),
            rec.direction,
            f"{rec.amount:.2f}",
            rec.bank_category,
            _normalize_whitespace(rec.description_raw),
        )
        duplicate_group_sizes[key] = duplicate_group_sizes.get(key, 0) + 1

    occurrence_by_group: dict[tuple[str, str, str, str, str], int] = {}
    updated_records: list[_Record] = []
    for rec in records:
        if rec.bank_reference_id:
            updated_records.append(rec)
            continue
        key = (
            rec.operation_datetime.replace(microsecond=0).isoformat(),
            rec.direction,
            f"{rec.amount:.2f}",
            rec.bank_category,
            _normalize_whitespace(rec.description_raw),
        )
        if duplicate_group_sizes.get(key, 0) <= 1:
            updated_records.append(rec)
            continue

        occurrence_by_group[key] = occurrence_by_group.get(key, 0) + 1
        occurrence = occurrence_by_group[key]
        payload = "|".join(("spbdup", *key, str(occurrence)))
        synthetic_ref = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]
        raw_data = dict(rec.raw_data)
        raw_data["bank_reference_id"] = synthetic_ref
        updated_records.append(
            _Record(
                page_number=rec.page_number,
                operation_datetime=rec.operation_datetime,
                posting_datetime=rec.posting_datetime,
                raw_text=rec.raw_text,
                raw_data=raw_data,
                amount=rec.amount,
                currency=rec.currency,
                direction=rec.direction,
                description_raw=rec.description_raw,
                bank_reference_id=synthetic_ref,
                bank_category=rec.bank_category,
                timestamp_precision=rec.timestamp_precision,
            )
        )

    return updated_records


def _parse_sber(*, pdf_text: PdfText, file_name: str, pdf_path: str) -> list[ParsedStatementBundle]:
    pages = [(i, page) for i, page in enumerate(pdf_text.pages, start=1)]
    page_text = pdf_text.full_text

    period = re.search(
        r"за период\s+(\d{2}\.\d{2}\.\d{4})\s*[—-]\s*(\d{2}\.\d{2}\.\d{4})",
        page_text,
        re.IGNORECASE,
    )
    period_start = _parse_date(period.group(1)) if period else None
    period_end = _parse_date(period.group(2)) if period else None

    statement_type = _detect_statement_type(file_name, page_text)
    # Sber PDFs include the account number with spaces; keep it readable.
    account_match = re.search(r"\b(\d{5}\s+\d{3}\s+\d\s+\d{4}\s+\d{7})\b", page_text)
    account_display = account_match.group(1) if account_match else (file_name or pdf_path)

    records = _parse_sber_records(pages, statement_type=statement_type)

    def _parse_sber_totals(
        text: str,
    ) -> tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        opening = None
        closing = None
        credits = None
        debits = None

        summary_match = re.search(
            r"ИТОГО\s+ПО\s+ОПЕРАЦИЯМ.*?(?:Расшифровка\s+операций|ДАТА\s+ОПЕРАЦИИ)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        summary_block = summary_match.group(0) if summary_match else text

        balance_values = re.findall(
            r"Остаток\s+средств\s+([0-9\s\u00a0.,]+)", summary_block, re.IGNORECASE
        )
        if len(balance_values) >= 2:
            opening = _parse_money(balance_values[0])
            closing = _parse_money(balance_values[1])
        elif len(balance_values) == 1:
            opening = _parse_money(balance_values[0])

        credit_values = re.findall(r"Пополнение\s+([0-9\s\u00a0.,]+)", summary_block, re.IGNORECASE)
        debit_values = re.findall(r"Списание\s+([0-9\s\u00a0.,]+)", summary_block, re.IGNORECASE)
        if credit_values:
            credits = _parse_money(credit_values[0])
        if debit_values:
            debits = _parse_money(debit_values[0])

        if credits is None or debits is None:
            card_totals = re.search(
                r"ВСЕГО\s+ПОПОЛНЕНИЙ\s+ВСЕГО\s+СПИСАНИЙ\s+([+\-–−]?\s*\d[\d\s\u00a0]*[.,]\d{1,2})\s+([+\-–−]?\s*\d[\d\s\u00a0]*[.,]\d{1,2})",
                summary_block,
                re.IGNORECASE | re.DOTALL,
            )
            if card_totals:
                credits = _parse_money(card_totals.group(1))
                debits = _parse_money(card_totals.group(2))

        if credits is None:
            credits_match = re.search(
                r"ВСЕГО\s+ПОПОЛНЕНИЙ\s*([+\-–−]?\s*\d[\d\s\u00a0]*[.,]\d{1,2})",
                summary_block,
                re.IGNORECASE | re.DOTALL,
            )
            if credits_match:
                credits = _parse_money(credits_match.group(1))

        if debits is None:
            debits_match = re.search(
                r"ВСЕГО\s+СПИСАНИЙ\s*([+\-–−]?\s*\d[\d\s\u00a0]*[.,]\d{1,2})",
                summary_block,
                re.IGNORECASE | re.DOTALL,
            )
            if debits_match:
                debits = _parse_money(debits_match.group(1))

        if credits is not None:
            credits = abs(credits)
        if debits is not None:
            debits = abs(debits)

        return opening, closing, credits, debits

    opening_balance, closing_balance, total_credits, total_debits = _parse_sber_totals(page_text)
    reconcile_status = _reconcile_status(
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        txs=records,
    )

    meta = ParsedStatement(
        provider=Provider.sber.value,
        statement_type=statement_type,
        currency="RUB",
        account_display=account_display,
        period_start=period_start,
        period_end=period_end,
        generated_at=None,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        reconcile_status=reconcile_status,
        parse_confidence=Decimal("0.75") if period_start and period_end else Decimal("0.55"),
    )

    rows: list[ParsedRow] = []
    txs: list[tuple[int, ParsedTransaction]] = []
    for row_index, rec in enumerate(records):
        parsed_row = ParsedRow(
            row_index=row_index,
            page_number=rec.page_number,
            raw_text=rec.raw_text,
            raw_data=rec.raw_data,
            operation_date=rec.operation_datetime,
            posting_date=rec.posting_datetime,
            amount=rec.amount,
            currency=rec.currency,
            direction=rec.direction,
            parse_confidence=Decimal("0.70"),
            timestamp_precision=rec.timestamp_precision,
        )
        rows.append(parsed_row)

        tx = ParsedTransaction(
            amount=rec.amount,
            currency=rec.currency,
            direction=rec.direction,
            operation_datetime=rec.operation_datetime,
            posting_datetime=rec.posting_datetime,
            description_raw=rec.description_raw,
            merchant_normalized=_extract_merchant_label(
                description_raw=rec.description_raw,
                bank_category=rec.bank_category,
                direction=rec.direction,
            ),
            bank_reference_id=rec.bank_reference_id,
            bank_category=rec.bank_category,
            meaning=TransactionMeaning.unknown.value,
            meaning_confidence=Decimal("0.00"),
            category="",
            tags=None,
            timestamp_precision=rec.timestamp_precision,
        )
        txs.append((row_index, tx))

    return [ParsedStatementBundle(meta=meta, rows=rows, txs=txs)]


def _parse_sber_records(
    pages: Sequence[tuple[int, str]], statement_type: str = "unknown"
) -> list[_Record]:
    in_table = False
    current: Optional[dict[str, Any]] = None
    records: list[_Record] = []

    record_start = re.compile(r"^(?P<date>\d{2}\.\d{2}\.\d{4})\s+(?P<rest>.+)$")
    continuation_line = re.compile(
        r"^(?P<date>\d{2}\.\d{2}\.\d{4})\s+"
        r"(?P<auth>(?=[0-9A-Za-z\-]*\d)[0-9A-Za-z\-]{4,})\s*(?P<tail>.*)$"
    )
    continuation_auth_token = re.compile(r"(?=[0-9A-Za-z\-]*\d)[0-9A-Za-z\-]{4,}")
    inline_reference = re.compile(r"№\s*(?P<ref>[0-9][0-9\-]{5,})")

    def is_description_continuation_line(line: str) -> bool:
        text = (line or "").strip()
        if not text:
            return False
        lower = text.lower()
        if "продолжение на следующей странице" in lower:
            return False
        # Pure numeric/value rows are balance columns, not narrative details.
        if not _has_letters(text):
            return False
        return True

    def infer_unsigned_card_direction(text: str) -> str:
        lower = text.lower()
        inflow_markers = (
            "перевод от ",
            "перевод на карту",
            "заработная плата",
            "зачисление",
            "пополнение",
            "sberbank onl@in vklad-karta",
        )
        outflow_markers = (
            "перевод для ",
            "перевод с карты",
            "оплата ",
            "покупка",
            "снятие наличных",
            "комиссия",
            "sberbank onl@in karta-vklad",
        )
        # Some lines include both "перевод с карты" and "перевод на карту";
        # treat these as outflow (from own card) to avoid false inflow.
        if any(marker in lower for marker in outflow_markers):
            return TransactionDirection.outflow.value
        if any(marker in lower for marker in inflow_markers):
            return TransactionDirection.inflow.value
        # Card rows without signs are overwhelmingly spends/transfers out.
        return TransactionDirection.outflow.value

    def infer_bank_category(text: str, *, statement_type: str) -> str:
        lower = text.lower()
        if "возврат" in lower and "qr" in lower:
            return "refund"
        if "перевод" in lower or "сбп" in lower or "sbp" in lower:
            return "transfer"
        if statement_type != "card" and ("к/с" in lower or "корреспондирующ" in lower):
            return "transfer"
        if "заработная плата" in lower or "зарплат" in lower:
            return "salary"
        if "кешбэк" in lower or "кэшбэк" in lower:
            return "cashback"
        return ""

    def flush() -> None:
        nonlocal current
        if not current:
            return
        raw_lines: list[str] = current["raw_lines"]
        joined = " ".join(raw_lines).strip()

        # Skip footer noise.
        if "Продолжение на следующей странице" in joined:
            joined = joined.replace("Продолжение на следующей странице", "").strip()

        # Extract amounts; the first is typically the transaction amount (balance column comes later).
        amount_tokens = _extract_candidate_amount_tokens(_DATE_RE.sub(" ", joined))
        if not amount_tokens:
            current = None
            return
        signed, has_explicit_sign = amount_tokens[0]
        if has_explicit_sign:
            direction = (
                TransactionDirection.inflow.value
                if signed > 0
                else TransactionDirection.outflow.value
            )
        elif statement_type == "card":
            direction = infer_unsigned_card_direction(joined)
        else:
            direction = (
                TransactionDirection.inflow.value
                if signed > 0
                else TransactionDirection.outflow.value
            )
        if signed == 0:
            direction = TransactionDirection.outflow.value
        amount_abs = abs(signed)

        op_dt = current["operation_datetime"]
        posting_dt = current.get("posting_datetime")
        bank_ref = current.get("bank_reference_id", "")
        bank_category = current.get("bank_category", "")
        description = current.get("description_raw", "")
        if not description:
            # Remove date/time and money tokens from a best-effort description.
            cleaned = re.sub(r"[+\-–−]?\s*\d[\d\s\u00a0]*[.,]\d{2}", " ", joined)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            description = cleaned
        description = _normalize_sber_description_for_cashflow(
            description_raw=description,
            direction=direction,
        )
        if not bank_ref:
            inline_ref_match = inline_reference.search(joined)
            if inline_ref_match:
                bank_ref = inline_ref_match.group("ref")
        if not bank_category:
            bank_category = infer_bank_category(
                f"{description} {joined}", statement_type=statement_type
            )

        records.append(
            _Record(
                page_number=current["page_number"],
                operation_datetime=op_dt,
                posting_datetime=posting_dt,
                raw_text=joined,
                raw_data=current.get("raw_data", {}),
                amount=amount_abs,
                currency="RUB",
                direction=direction,
                description_raw=description,
                bank_reference_id=bank_ref,
                bank_category=bank_category,
                timestamp_precision=current.get("timestamp_precision", "unknown"),
            )
        )
        current = None

    for page_number, line in _iter_page_lines(pages):
        lower = line.lower()
        if "расшифровка операций" in lower or "наименование операции" in lower:
            in_table = True
            continue
        if not in_table:
            continue

        if (
            lower.startswith("выписка ")
            or lower.startswith("дата")
            or lower in {"операции", "сумма", "остаток"}
        ):
            continue
        if "страница" in lower:
            continue

        continuation_match = continuation_line.match(line)
        if current is not None and continuation_match:
            current["raw_lines"].append(line)
            posting_dt = _parse_date(continuation_match.group("date"))
            if posting_dt is not None:
                current["posting_datetime"] = posting_dt
            current["bank_reference_id"] = continuation_match.group("auth")
            tail = (continuation_match.group("tail") or "").strip()
            if tail:
                current["description_raw"] = " ".join(
                    [current.get("description_raw", ""), tail]
                ).strip()
            continue

        m = record_start.match(line)
        if m:
            # New record starts on any line that begins with a date once we're inside the operations table.
            flush()

            date_str = m.group("date")
            rest = (m.group("rest") or "").strip()

            # Card/payment statements: first line includes time and amount.
            time_match = _TIME_RE.search(rest)
            operation_dt: Optional[datetime]
            if time_match:
                operation_dt = _combine_date_time(date_str, time_match.group(0))
            else:
                operation_dt = _parse_date(date_str)
            if operation_dt is None:
                continue

            bank_ref = ""
            posting_dt = None
            desc = rest

            # Some tables include a second line "DD.MM.YYYY <auth_code> <description>".
            # We'll parse that in the append phase.
            current = {
                "page_number": page_number,
                "operation_datetime": operation_dt,
                "posting_datetime": posting_dt,
                "bank_reference_id": bank_ref,
                "bank_category": "",
                "description_raw": desc,
                "raw_lines": [line],
                "raw_data": {"provider": Provider.sber.value},
                "timestamp_precision": "exact" if time_match else "date_only",
            }
            continue

        if current is None:
            continue

        current["raw_lines"].append(line)

        # Try to capture posting date + auth code on lines like "DD.MM.YYYY 123456 ...".
        tokens = line.split()
        auth_token = tokens[1].strip(".,;") if len(tokens) >= 2 else ""
        if (
            len(tokens) >= 2
            and _DATE_RE.fullmatch(tokens[0])
            and continuation_auth_token.fullmatch(auth_token)
        ):
            posting_dt = _parse_date(tokens[0])
            if posting_dt is not None:
                current["posting_datetime"] = posting_dt
            current["bank_reference_id"] = auth_token
            tail = " ".join(tokens[2:]).strip()
            if tail:
                current["description_raw"] = " ".join(
                    [current.get("description_raw", ""), tail]
                ).strip()
            continue

        if is_description_continuation_line(line):
            current["description_raw"] = " ".join(
                [current.get("description_raw", ""), line]
            ).strip()

    flush()
    return records
