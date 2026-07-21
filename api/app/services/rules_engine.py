from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import Rule, Transaction


@dataclass(frozen=True)
class RuleApplicationSample:
    transaction_id: str
    matched_rule_ids: list[str]
    before_category: str
    after_category: str
    before_tags: list[str]
    after_tags: list[str]
    before_meaning: str
    after_meaning: str
    before_review_status: str
    after_review_status: str


@dataclass(frozen=True)
class RuleApplicationResult:
    transactions_scanned: int
    transactions_matched: int
    transactions_changed: int
    transactions_updated: int
    sample: list[RuleApplicationSample]


@dataclass(frozen=True)
class RuleBootstrapResult:
    default_rules_created: int
    transactions_updated: int


_DEFAULT_RULE_PRIORITY_BASE = 1_000_000
_DEFAULT_RULE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "name": "[system] Internal transfer fallback",
        "conditions": {"meaning": "internal_transfer", "category_empty": True},
        "actions": {"set_category": "Transfer"},
    },
    {
        "name": "[system] Outflow fallback",
        "conditions": {"direction": "out", "category_empty": True},
        "actions": {"set_category": "Spending"},
    },
    {
        "name": "[system] Inflow fallback",
        "conditions": {"direction": "in", "category_empty": True},
        "actions": {"set_category": "Income"},
    },
)


def _as_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _normalize_tags(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        items = value
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        tag = str(item).strip()
        if not tag:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result


def _transaction_match_text(tx: Transaction) -> str:
    parts = [
        tx.description_raw or "",
        tx.merchant_normalized or "",
        tx.bank_category or "",
    ]
    return " ".join(p for p in parts if p).strip().lower()


def _matches_conditions(tx: Transaction, conditions: dict[str, Any]) -> bool:
    direction = conditions.get("direction")
    if isinstance(direction, str) and direction and tx.direction != direction:
        return False

    currency = conditions.get("currency")
    if isinstance(currency, str) and currency and (tx.currency or "RUB") != currency:
        return False

    meaning = conditions.get("meaning")
    if isinstance(meaning, str) and meaning and (tx.meaning or "unknown") != meaning:
        return False

    category = conditions.get("category")
    if isinstance(category, str) and category and (tx.category or "") != category:
        return False

    category_empty = conditions.get("category_empty")
    if isinstance(category_empty, bool):
        is_empty = not (tx.category or "").strip()
        if category_empty != is_empty:
            return False

    desc_contains = conditions.get("description_contains")
    if isinstance(desc_contains, str) and desc_contains:
        if desc_contains.lower() not in (tx.description_raw or "").lower():
            return False

    merchant_contains = conditions.get("merchant_contains")
    if isinstance(merchant_contains, str) and merchant_contains:
        if merchant_contains.lower() not in (tx.merchant_normalized or "").lower():
            return False

    bank_category_contains = conditions.get("bank_category_contains")
    if isinstance(bank_category_contains, str) and bank_category_contains:
        if bank_category_contains.lower() not in (tx.bank_category or "").lower():
            return False

    amount = _as_decimal(tx.amount)
    min_amount = _as_decimal(conditions.get("min_amount"))
    if min_amount is not None and (amount is None or amount < min_amount):
        return False

    max_amount = _as_decimal(conditions.get("max_amount"))
    if max_amount is not None and (amount is None or amount > max_amount):
        return False

    tags_any = conditions.get("tags_any")
    if isinstance(tags_any, list) and tags_any:
        tag_set = set(_normalize_tags(tx.tags))
        if not any(str(item).strip() in tag_set for item in tags_any if item is not None):
            return False

    tags_all = conditions.get("tags_all")
    if isinstance(tags_all, list) and tags_all:
        tag_set = set(_normalize_tags(tx.tags))
        if any(str(item).strip() not in tag_set for item in tags_all if item is not None):
            return False

    tags_none = conditions.get("tags_none")
    if isinstance(tags_none, list) and tags_none:
        tag_set = set(_normalize_tags(tx.tags))
        if any(str(item).strip() in tag_set for item in tags_none if item is not None):
            return False

    return True


def _rule_matches_transaction(tx: Transaction, rule: Rule) -> bool:
    if not rule.enabled:
        return False
    conditions = rule.conditions or {}
    pattern = (rule.pattern or "").strip()
    if pattern:
        if pattern.lower() not in _transaction_match_text(tx):
            return False
    elif not conditions:
        return False
    if isinstance(conditions, dict) and conditions:
        return _matches_conditions(tx, conditions)
    return True


def _load_rules(db: Session) -> list[Rule]:
    return (
        db.query(Rule)
        .filter(Rule.enabled.is_(True))
        .order_by(Rule.priority.asc(), Rule.created_at.asc(), Rule.id.asc())
        .all()
    )


def _apply_directional_category_guardrail(
    *,
    tx: Transaction,
    category: str,
    meaning: str,
    review_status: str,
) -> str:
    if meaning == "internal_transfer":
        return category
    if review_status.strip().lower() == "reviewed":
        return category

    normalized = category.strip().lower()
    direction = (tx.direction or "").strip().lower()

    if direction == "out" and normalized == "income":
        return "Spending"
    if direction == "in" and normalized == "spending":
        return "Income"
    return category


def _compute_after_state(
    *,
    tx: Transaction,
    rules: list[Rule],
) -> tuple[str, list[str], str, str, list[str]]:
    matched_rule_ids: list[str] = []

    after_category = tx.category or ""
    after_tags = _normalize_tags(tx.tags)
    after_meaning = tx.meaning or "unknown"
    after_review_status = tx.review_status or "reviewed"

    category_set_by_rule = False
    tags_replaced_by_rule = False
    meaning_set_by_rule = False
    review_set_by_rule = False

    for rule in rules:
        if not _rule_matches_transaction(tx, rule):
            continue

        matched_rule_ids.append(rule.id)
        actions = rule.actions if isinstance(rule.actions, dict) else {}

        if not category_set_by_rule:
            set_category = actions.get("set_category")
            if isinstance(set_category, str) and set_category.strip():
                after_category = set_category.strip()
                category_set_by_rule = True

        if not tags_replaced_by_rule:
            set_tags = actions.get("set_tags")
            if set_tags is not None:
                after_tags = _normalize_tags(set_tags)
                tags_replaced_by_rule = True

        add_tags = actions.get("add_tags")
        if isinstance(add_tags, list):
            for item in add_tags:
                tag = str(item).strip() if item is not None else ""
                if not tag:
                    continue
                if tag in after_tags:
                    continue
                after_tags.append(tag)

        remove_tags = actions.get("remove_tags")
        if isinstance(remove_tags, list) and after_tags:
            remove_set = {str(item).strip() for item in remove_tags if item is not None}
            if remove_set:
                after_tags = [tag for tag in after_tags if tag not in remove_set]

        if not meaning_set_by_rule:
            set_meaning = actions.get("set_meaning")
            if isinstance(set_meaning, str) and set_meaning.strip():
                after_meaning = set_meaning.strip()
                meaning_set_by_rule = True

        if not review_set_by_rule:
            set_review_status = actions.get("set_review_status")
            if isinstance(set_review_status, str) and set_review_status.strip():
                after_review_status = set_review_status.strip()
                review_set_by_rule = True

    after_category = _apply_directional_category_guardrail(
        tx=tx,
        category=after_category,
        meaning=after_meaning,
        review_status=after_review_status,
    )

    return after_category, after_tags, after_meaning, after_review_status, matched_rule_ids


def _build_transaction_query(
    db: Session,
    *,
    q: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    direction: Optional[str] = None,
    meaning: Optional[str] = None,
    category: Optional[str] = None,
    include_transfers: bool = False,
):
    query = db.query(Transaction)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Transaction.description_raw.ilike(like),
                Transaction.merchant_normalized.ilike(like),
                Transaction.bank_category.ilike(like),
                Transaction.category.ilike(like),
            )
        )

    if start:
        query = query.filter(Transaction.operation_datetime >= start)
    if end:
        query = query.filter(Transaction.operation_datetime <= end)
    if direction:
        query = query.filter(Transaction.direction == direction)
    if meaning:
        query = query.filter(Transaction.meaning == meaning)
    if category:
        query = query.filter(Transaction.category == category)
    if not include_transfers:
        query = query.filter(
            or_(Transaction.meaning.is_(None), Transaction.meaning != "internal_transfer")
        )

    return query


def preview_rule_application_in_session(
    db: Session,
    *,
    q: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    direction: Optional[str] = None,
    meaning: Optional[str] = None,
    category: Optional[str] = None,
    include_transfers: bool = False,
    limit: int = 5000,
    offset: int = 0,
    sample_limit: int = 20,
) -> RuleApplicationResult:
    rules = _load_rules(db)

    txs = (
        _build_transaction_query(
            db,
            q=q,
            start=start,
            end=end,
            direction=direction,
            meaning=meaning,
            category=category,
            include_transfers=include_transfers,
        )
        .order_by(
            Transaction.operation_datetime.desc().nullslast(),
            Transaction.created_at.desc(),
            Transaction.id.asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    scanned = 0
    matched = 0
    changed = 0
    sample: list[RuleApplicationSample] = []

    for tx in txs:
        scanned += 1
        after_category, after_tags, after_meaning, after_review_status, matched_rule_ids = (
            _compute_after_state(tx=tx, rules=rules)
        )
        before_category = tx.category or ""
        before_tags = _normalize_tags(tx.tags)
        before_meaning = tx.meaning or "unknown"
        before_review_status = tx.review_status or "reviewed"
        if matched_rule_ids:
            matched += 1
        if (
            before_category != after_category
            or before_tags != after_tags
            or before_meaning != after_meaning
            or before_review_status != after_review_status
        ):
            changed += 1
            if len(sample) < sample_limit:
                sample.append(
                    RuleApplicationSample(
                        transaction_id=tx.id,
                        matched_rule_ids=matched_rule_ids,
                        before_category=before_category,
                        after_category=after_category,
                        before_tags=before_tags,
                        after_tags=after_tags,
                        before_meaning=before_meaning,
                        after_meaning=after_meaning,
                        before_review_status=before_review_status,
                        after_review_status=after_review_status,
                    )
                )

    return RuleApplicationResult(
        transactions_scanned=scanned,
        transactions_matched=matched,
        transactions_changed=changed,
        transactions_updated=0,
        sample=sample,
    )


def apply_rules_in_session(
    db: Session,
    *,
    q: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    direction: Optional[str] = None,
    meaning: Optional[str] = None,
    category: Optional[str] = None,
    include_transfers: bool = False,
    limit: int = 50000,
    offset: int = 0,
    sample_limit: int = 20,
    dry_run: bool = False,
) -> RuleApplicationResult:
    rules = _load_rules(db)

    query = (
        _build_transaction_query(
            db,
            q=q,
            start=start,
            end=end,
            direction=direction,
            meaning=meaning,
            category=category,
            include_transfers=include_transfers,
        )
        .order_by(
            Transaction.operation_datetime.desc().nullslast(),
            Transaction.created_at.desc(),
            Transaction.id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )

    scanned = 0
    matched = 0
    changed = 0
    updated = 0
    sample: list[RuleApplicationSample] = []

    for tx in query.all():
        scanned += 1
        after_category, after_tags, after_meaning, after_review_status, matched_rule_ids = (
            _compute_after_state(tx=tx, rules=rules)
        )
        before_category = tx.category or ""
        before_tags = _normalize_tags(tx.tags)
        before_meaning = tx.meaning or "unknown"
        before_review_status = tx.review_status or "reviewed"
        if matched_rule_ids:
            matched += 1
        if (
            before_category == after_category
            and before_tags == after_tags
            and before_meaning == after_meaning
            and before_review_status == after_review_status
        ):
            continue

        changed += 1
        if len(sample) < sample_limit:
            sample.append(
                RuleApplicationSample(
                    transaction_id=tx.id,
                    matched_rule_ids=matched_rule_ids,
                    before_category=before_category,
                    after_category=after_category,
                    before_tags=before_tags,
                    after_tags=after_tags,
                    before_meaning=before_meaning,
                    after_meaning=after_meaning,
                    before_review_status=before_review_status,
                    after_review_status=after_review_status,
                )
            )

        if dry_run:
            continue

        tx.category = after_category
        tx.tags = after_tags or None
        tx.meaning = after_meaning
        tx.review_status = after_review_status
        updated += 1

    return RuleApplicationResult(
        transactions_scanned=scanned,
        transactions_matched=matched,
        transactions_changed=changed,
        transactions_updated=updated,
        sample=sample,
    )


def bootstrap_default_rules_if_needed(
    db: Session,
    *,
    apply_limit: int = 200000,
) -> RuleBootstrapResult:
    if db.query(Rule.id).first() is not None:
        return RuleBootstrapResult(default_rules_created=0, transactions_updated=0)

    for index, template in enumerate(_DEFAULT_RULE_TEMPLATES):
        db.add(
            Rule(
                name=str(template["name"]),
                pattern="",
                conditions=template["conditions"],
                actions=template["actions"],
                priority=_DEFAULT_RULE_PRIORITY_BASE + index,
                enabled=True,
            )
        )

    db.flush()
    apply_result = apply_rules_in_session(db, include_transfers=True, limit=apply_limit)
    return RuleBootstrapResult(
        default_rules_created=len(_DEFAULT_RULE_TEMPLATES),
        transactions_updated=apply_result.transactions_updated,
    )


def apply_rules_to_transactions(
    db: Session,
    *,
    transactions: list[Transaction],
    sample_limit: int = 20,
    dry_run: bool = False,
) -> RuleApplicationResult:
    rules = _load_rules(db)
    if not transactions:
        return RuleApplicationResult(
            transactions_scanned=len(transactions),
            transactions_matched=0,
            transactions_changed=0,
            transactions_updated=0,
            sample=[],
        )

    scanned = 0
    matched = 0
    changed = 0
    updated = 0
    sample: list[RuleApplicationSample] = []

    for tx in transactions:
        scanned += 1
        after_category, after_tags, after_meaning, after_review_status, matched_rule_ids = (
            _compute_after_state(tx=tx, rules=rules)
        )
        before_category = tx.category or ""
        before_tags = _normalize_tags(tx.tags)
        before_meaning = tx.meaning or "unknown"
        before_review_status = tx.review_status or "reviewed"

        if matched_rule_ids:
            matched += 1

        if (
            before_category == after_category
            and before_tags == after_tags
            and before_meaning == after_meaning
            and before_review_status == after_review_status
        ):
            continue

        changed += 1
        if len(sample) < sample_limit:
            sample.append(
                RuleApplicationSample(
                    transaction_id=tx.id,
                    matched_rule_ids=matched_rule_ids,
                    before_category=before_category,
                    after_category=after_category,
                    before_tags=before_tags,
                    after_tags=after_tags,
                    before_meaning=before_meaning,
                    after_meaning=after_meaning,
                    before_review_status=before_review_status,
                    after_review_status=after_review_status,
                )
            )

        if dry_run:
            continue

        tx.category = after_category
        tx.tags = after_tags or None
        tx.meaning = after_meaning
        tx.review_status = after_review_status
        updated += 1

    return RuleApplicationResult(
        transactions_scanned=scanned,
        transactions_matched=matched,
        transactions_changed=changed,
        transactions_updated=updated,
        sample=sample,
    )
