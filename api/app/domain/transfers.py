from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping, Sequence


_TRANSFER_HINT_RE = re.compile(
    r"("
    r"\bперевод\b|"
    r"\bсбп\b|"
    r"\bsbp\b|"
    r"между\s+своими|"
    r"собственн(ых|ые)\s+средств|"
    r"\btransfer\b|"
    r"between\s+accounts|"
    r"\bp2p\b"
    r")",
    re.IGNORECASE,
)

_KNOWN_INTERNAL_LANE_MARKER_RE = re.compile(
    r"(vklad[\s\-]?karta|karta[\s\-]?vklad)",
    re.IGNORECASE,
)

_GENERIC_OUTFLOW_DESCRIPTOR_RE = re.compile(
    r"^\s*(списание|debit|withdrawal)\b",
    re.IGNORECASE,
)

_COUNTERPARTY_TOKEN_RE = re.compile(r"[0-9a-zа-я]{3,}", re.IGNORECASE)
_COUNTERPARTY_STOPWORDS = {
    "перевод",
    "сбп",
    "sbp",
    "transfer",
    "between",
    "accounts",
    "между",
    "своими",
    "счет",
    "счета",
    "счетами",
    "списание",
    "зачисление",
    "incoming",
    "outgoing",
    "payment",
    "card",
    "карту",
    "карта",
    "карты",
    "своих",
    "funds",
    "own",
    "account",
    "from",
    "to",
}


@dataclass(frozen=True)
class TransferTx:
    id: str
    account_id: str
    direction: str
    currency: str
    amount_cents: int
    timestamp: datetime
    description_raw: str
    bank_category: str = ""
    bank_reference_id: str = ""


@dataclass(frozen=True)
class TransferLanePrior:
    account_out_id: str
    account_in_id: str
    confirmations_count: int
    typical_delay_window_seconds: float
    out_description_patterns: tuple[str, ...]
    in_description_patterns: tuple[str, ...]


@dataclass(frozen=True)
class ScoredTransferPair:
    transaction_out_id: str
    transaction_in_id: str
    score: float
    rationale: str
    fee_amount: float | None
    amount_delta_cents: int
    time_delta_seconds: float
    signed_delta_seconds: float
    out_hint: bool
    in_hint: bool
    bank_reference_match: bool
    outflow_requires_stronger_evidence: bool
    out_account_id: str = ""
    in_account_id: str = ""
    counterparty_overlap_count: int = 0
    out_known_internal_lane_marker: bool = False
    in_known_internal_lane_marker: bool = False
    lane_prior_confirmations: int = 0
    lane_prior_window_seconds: float | None = None
    lane_prior_pattern_hits_out: int = 0
    lane_prior_pattern_hits_in: int = 0
    lane_prior_bonus: float = 0.0


@dataclass(frozen=True)
class SelectedTransferLink:
    transaction_out_id: str
    transaction_in_id: str
    status: str
    score: float
    rationale: str
    fee_amount: float | None


@dataclass(frozen=True)
class TransferSelection:
    auto_links: list[SelectedTransferLink]
    suggested_links: list[SelectedTransferLink]


def _is_transfer_like(text: str) -> bool:
    if not text:
        return False
    return bool(_TRANSFER_HINT_RE.search(text))


def _has_known_internal_lane_marker(text: str) -> bool:
    if not text:
        return False
    return bool(_KNOWN_INTERNAL_LANE_MARKER_RE.search(text))


def _is_generic_outflow_without_hint(*, description_raw: str, out_hint: bool) -> bool:
    if out_hint:
        return False
    return bool(_GENERIC_OUTFLOW_DESCRIPTOR_RE.search(description_raw or ""))


def _counterparty_token_set(text: str) -> set[str]:
    if not text:
        return set()
    tokens = {token.lower() for token in _COUNTERPARTY_TOKEN_RE.findall(text)}
    return {token for token in tokens if token not in _COUNTERPARTY_STOPWORDS}


def _counterparty_overlap_count(out_text: str, in_text: str) -> int:
    out_tokens = _counterparty_token_set(out_text)
    in_tokens = _counterparty_token_set(in_text)
    if not out_tokens or not in_tokens:
        return 0
    return len(out_tokens & in_tokens)


def _account_pair_key(pair: ScoredTransferPair) -> tuple[str, str] | None:
    if not pair.out_account_id or not pair.in_account_id:
        return None
    return (pair.out_account_id, pair.in_account_id)


def _build_account_pair_forward_counts(
    pairs: Sequence[ScoredTransferPair],
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for pair in pairs:
        if pair.signed_delta_seconds < 0:
            continue
        key = _account_pair_key(pair)
        if key is None:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _build_account_pair_total_counts(
    pairs: Sequence[ScoredTransferPair],
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for pair in pairs:
        key = _account_pair_key(pair)
        if key is None:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _effective_matching_window(
    *,
    base_window: timedelta,
    lane_prior: TransferLanePrior | None,
) -> timedelta:
    if lane_prior is None or lane_prior.confirmations_count <= 0:
        return base_window

    lane_window_seconds = max(0.0, float(lane_prior.typical_delay_window_seconds))
    if lane_window_seconds <= 0.0:
        return base_window

    dynamic_window_seconds = min(
        (lane_window_seconds * 1.8) + (6 * 60 * 60),
        5 * 24 * 60 * 60,
    )
    return max(base_window, timedelta(seconds=dynamic_window_seconds))


def _lane_pattern_hits(
    *,
    text: str,
    patterns: tuple[str, ...],
) -> int:
    if not text or not patterns:
        return 0
    tokens = _counterparty_token_set(text)
    if not tokens:
        return 0
    return len(tokens & set(patterns))


def _lane_prior_bonus(
    *,
    lane_prior_confirmations: int,
    lane_prior_pattern_hits_out: int,
    lane_prior_pattern_hits_in: int,
    lane_window_match: bool,
) -> float:
    if lane_prior_confirmations <= 0:
        return 0.0

    bonus = min(0.06, lane_prior_confirmations * 0.008)
    if lane_prior_pattern_hits_out > 0:
        bonus += min(0.03, 0.015 * lane_prior_pattern_hits_out)
    if lane_prior_pattern_hits_in > 0:
        bonus += min(0.03, 0.015 * lane_prior_pattern_hits_in)
    if lane_window_match:
        bonus += 0.03
    return min(0.12, bonus)


def _is_high_confidence_pair_for_auto(
    pair: ScoredTransferPair,
    *,
    auto_threshold: float,
) -> bool:
    return (
        pair.score >= auto_threshold
        and (pair.out_hint or pair.in_hint)
        and pair.amount_delta_cents == 0
        and not pair.outflow_requires_stronger_evidence
    )


def _resolve_two_by_two_conflict(
    *,
    pair: ScoredTransferPair,
    out_pairs: Sequence[ScoredTransferPair],
    in_pairs: Sequence[ScoredTransferPair],
    pair_lookup: Mapping[tuple[str, str], ScoredTransferPair],
    auto_threshold: float,
) -> tuple[bool, str]:
    if len(out_pairs) < 2 or len(in_pairs) < 2:
        return False, "none"

    top_out = out_pairs[0]
    runner_up_out = out_pairs[1]
    top_in = in_pairs[0]
    runner_up_in = in_pairs[1]
    if (
        top_out.transaction_out_id != pair.transaction_out_id
        or top_out.transaction_in_id != pair.transaction_in_id
        or top_in.transaction_out_id != pair.transaction_out_id
        or top_in.transaction_in_id != pair.transaction_in_id
    ):
        return False, "none"

    alt_out_id = runner_up_in.transaction_out_id
    alt_in_id = runner_up_out.transaction_in_id
    if alt_out_id == pair.transaction_out_id or alt_in_id == pair.transaction_in_id:
        return False, "none"

    paired_alt = pair_lookup.get((alt_out_id, alt_in_id))
    if paired_alt is None:
        return False, "none"

    quartet = [pair, runner_up_out, runner_up_in, paired_alt]
    if any(
        not _is_high_confidence_pair_for_auto(candidate, auto_threshold=auto_threshold)
        for candidate in quartet
    ):
        return False, "none"

    tx_ids = {
        pair.transaction_out_id,
        pair.transaction_in_id,
        alt_out_id,
        alt_in_id,
    }
    if len(tx_ids) != 4:
        return False, "none"

    direct_total = pair.score + paired_alt.score
    cross_total = runner_up_out.score + runner_up_in.score
    if direct_total > cross_total:
        return True, "2x2_total_score"

    if abs(direct_total - cross_total) > 1e-9:
        return False, "none"

    score_values = [candidate.score for candidate in quartet]
    if (max(score_values) - min(score_values)) > 1e-9:
        return False, "none"

    if any(candidate.time_delta_seconds > 30.0 for candidate in quartet):
        return False, "none"

    return True, "2x2_deterministic_symmetry"


def _pair_score(
    *,
    delta_seconds: float,
    out_hint: bool,
    in_hint: bool,
    same_day: bool,
    amount_score: float,
) -> float:
    score = 0.20 + max(0.0, min(1.0, amount_score)) * 0.35
    if delta_seconds <= 5 * 60:
        score += 0.25
    elif delta_seconds <= 30 * 60:
        score += 0.18
    elif delta_seconds <= 2 * 60 * 60:
        score += 0.12
    elif delta_seconds <= 24 * 60 * 60:
        score += 0.05
    else:
        score += 0.02
    if out_hint:
        score += 0.10
    if in_hint:
        score += 0.10
    if same_day:
        score += 0.04
    return min(score, 0.99)


def score_transfer_pair(
    tx_out: TransferTx,
    tx_in: TransferTx,
    *,
    window: timedelta = timedelta(days=2),
    lane_prior: TransferLanePrior | None = None,
    max_fee_abs_cents: int = 1000,
    max_fee_ratio: Decimal = Decimal("0.005"),
) -> ScoredTransferPair | None:
    if tx_out.direction != "out" or tx_in.direction != "in":
        return None
    if tx_out.account_id == tx_in.account_id:
        return None
    if tx_out.currency != tx_in.currency:
        return None

    signed_delta_seconds = (tx_in.timestamp - tx_out.timestamp).total_seconds()
    delta = abs(tx_in.timestamp - tx_out.timestamp)
    effective_window = _effective_matching_window(base_window=window, lane_prior=lane_prior)
    if delta > effective_window:
        return None

    amount_delta_cents = abs(tx_out.amount_cents - tx_in.amount_cents)
    base_amount_cents = max(tx_out.amount_cents, tx_in.amount_cents)
    allowed_delta = min(max_fee_abs_cents, max(1, int(Decimal(base_amount_cents) * max_fee_ratio)))
    if amount_delta_cents > allowed_delta:
        return None

    amount_score = (
        1.0
        if allowed_delta == 0
        else max(0.7, min(1.0, 1.0 - (amount_delta_cents / allowed_delta)))
    )
    out_hint_text = " ".join([tx_out.description_raw or "", tx_out.bank_category or ""]).strip()
    in_hint_text = " ".join([tx_in.description_raw or "", tx_in.bank_category or ""]).strip()
    out_hint = _is_transfer_like(out_hint_text)
    in_hint = _is_transfer_like(in_hint_text)
    out_known_lane_marker = _has_known_internal_lane_marker(tx_out.description_raw)
    in_known_lane_marker = _has_known_internal_lane_marker(tx_in.description_raw)
    out_ref = (tx_out.bank_reference_id or "").strip().lower()
    in_ref = (tx_in.bank_reference_id or "").strip().lower()
    bank_reference_match = bool(out_ref and in_ref and out_ref == in_ref)
    outflow_requires_stronger_evidence = _is_generic_outflow_without_hint(
        description_raw=tx_out.description_raw,
        out_hint=out_hint,
    )
    counterparty_overlap_count = _counterparty_overlap_count(
        tx_out.description_raw,
        tx_in.description_raw,
    )
    same_day = tx_out.timestamp.date() == tx_in.timestamp.date()
    lane_prior_confirmations = 0
    lane_prior_window_seconds: float | None = None
    lane_prior_pattern_hits_out = 0
    lane_prior_pattern_hits_in = 0
    if lane_prior is not None and lane_prior.confirmations_count > 0:
        lane_prior_confirmations = lane_prior.confirmations_count
        lane_prior_window_seconds = max(0.0, float(lane_prior.typical_delay_window_seconds))
        lane_prior_pattern_hits_out = _lane_pattern_hits(
            text=out_hint_text,
            patterns=lane_prior.out_description_patterns,
        )
        lane_prior_pattern_hits_in = _lane_pattern_hits(
            text=in_hint_text,
            patterns=lane_prior.in_description_patterns,
        )
    lane_window_match = bool(
        lane_prior_window_seconds and delta.total_seconds() <= lane_prior_window_seconds
    )
    lane_prior_bonus = _lane_prior_bonus(
        lane_prior_confirmations=lane_prior_confirmations,
        lane_prior_pattern_hits_out=lane_prior_pattern_hits_out,
        lane_prior_pattern_hits_in=lane_prior_pattern_hits_in,
        lane_window_match=lane_window_match,
    )

    score = _pair_score(
        delta_seconds=delta.total_seconds(),
        out_hint=out_hint,
        in_hint=in_hint,
        same_day=same_day,
        amount_score=amount_score,
    )
    score = min(0.99, score + lane_prior_bonus)

    rationale = (
        "amount_match="
        f"{amount_delta_cents / 100:.2f} "
        f"(score={amount_score:.2f}); "
        f"dt={int(delta.total_seconds())}s; "
        f"hints={int(out_hint)}/{int(in_hint)}; "
        f"same_day={int(same_day)}; "
        f"in_after_out={int(signed_delta_seconds >= 0)}; "
        f"ref_match={int(bank_reference_match)}; "
        f"out_generic_no_hint={int(outflow_requires_stronger_evidence)}; "
        f"counterparty_overlap={counterparty_overlap_count}; "
        f"known_lane_marker={int(out_known_lane_marker)}/{int(in_known_lane_marker)}; "
        f"lane_prior_confirmations={lane_prior_confirmations}; "
        f"lane_prior_window_s={int(lane_prior_window_seconds or 0.0)}; "
        f"lane_prior_pattern_hits={lane_prior_pattern_hits_out}/{lane_prior_pattern_hits_in}; "
        f"lane_prior_bonus={lane_prior_bonus:.3f}; "
        f"match_window_s={int(effective_window.total_seconds())}"
    )

    return ScoredTransferPair(
        transaction_out_id=tx_out.id,
        transaction_in_id=tx_in.id,
        score=score,
        rationale=rationale,
        fee_amount=(amount_delta_cents / 100.0) if amount_delta_cents > 0 else None,
        amount_delta_cents=amount_delta_cents,
        time_delta_seconds=delta.total_seconds(),
        signed_delta_seconds=signed_delta_seconds,
        out_hint=out_hint,
        in_hint=in_hint,
        bank_reference_match=bank_reference_match,
        outflow_requires_stronger_evidence=outflow_requires_stronger_evidence,
        out_account_id=tx_out.account_id,
        in_account_id=tx_in.account_id,
        counterparty_overlap_count=counterparty_overlap_count,
        out_known_internal_lane_marker=out_known_lane_marker,
        in_known_internal_lane_marker=in_known_lane_marker,
        lane_prior_confirmations=lane_prior_confirmations,
        lane_prior_window_seconds=lane_prior_window_seconds,
        lane_prior_pattern_hits_out=lane_prior_pattern_hits_out,
        lane_prior_pattern_hits_in=lane_prior_pattern_hits_in,
        lane_prior_bonus=lane_prior_bonus,
    )


def _pair_rank_key(
    pair: ScoredTransferPair,
    *,
    account_pair_forward_counts: Mapping[tuple[str, str], int] | None = None,
) -> tuple[float | int | str, ...]:
    account_pair_forward_count = 0
    if account_pair_forward_counts:
        key = _account_pair_key(pair)
        if key is not None:
            account_pair_forward_count = account_pair_forward_counts.get(key, 0)

    return (
        -pair.score,
        -(int(pair.out_hint) + int(pair.in_hint)),
        -int(pair.bank_reference_match),
        -pair.counterparty_overlap_count,
        -int(pair.signed_delta_seconds >= 0),
        -account_pair_forward_count,
        -pair.lane_prior_confirmations,
        pair.time_delta_seconds,
        pair.amount_delta_cents,
        pair.transaction_out_id,
        pair.transaction_in_id,
    )


def _is_ambiguous_candidate_set(
    pairs: Sequence[ScoredTransferPair],
    *,
    ambiguity_margin: float,
    time_tiebreak_seconds: float = 120.0,
    focus_threshold: float | None = None,
    account_pair_forward_counts: Mapping[tuple[str, str], int] | None = None,
) -> tuple[bool, str]:
    filtered_pairs = list(pairs)
    if focus_threshold is not None:
        filtered_pairs = [pair for pair in filtered_pairs if pair.score >= focus_threshold]

    if len(filtered_pairs) <= 1:
        return False, "single"

    first = filtered_pairs[0]
    second = filtered_pairs[1]

    if (first.score - second.score) >= ambiguity_margin:
        return False, "score_gap"

    if first.bank_reference_match != second.bank_reference_match:
        return (not first.bank_reference_match), "bank_ref"

    first_hints = int(first.out_hint) + int(first.in_hint)
    second_hints = int(second.out_hint) + int(second.in_hint)
    if first_hints != second_hints:
        return (first_hints < second_hints), "hint_count"

    if first.counterparty_overlap_count != second.counterparty_overlap_count:
        return (
            first.counterparty_overlap_count < second.counterparty_overlap_count
        ), "counterparty"

    first_forward = first.signed_delta_seconds >= 0
    second_forward = second.signed_delta_seconds >= 0
    if first_forward != second_forward:
        return (not first_forward), "chronology"

    first_pair_key = _account_pair_key(first)
    second_pair_key = _account_pair_key(second)
    first_pair_forward_count = (
        account_pair_forward_counts.get(first_pair_key, 0)
        if account_pair_forward_counts and first_pair_key is not None
        else 0
    )
    second_pair_forward_count = (
        account_pair_forward_counts.get(second_pair_key, 0)
        if account_pair_forward_counts and second_pair_key is not None
        else 0
    )
    if first_pair_forward_count != second_pair_forward_count:
        return (first_pair_forward_count < second_pair_forward_count), "account_pair_chronology"

    if first.lane_prior_confirmations != second.lane_prior_confirmations:
        return (first.lane_prior_confirmations < second.lane_prior_confirmations), "lane_prior"

    if (second.time_delta_seconds - first.time_delta_seconds) >= time_tiebreak_seconds:
        return False, "time_delta"

    if first.amount_delta_cents != second.amount_delta_cents:
        return (first.amount_delta_cents > second.amount_delta_cents), "amount_delta"

    return True, "none"


_EXACT_COMPONENT_MAX_OUTFLOWS = 10
_EXACT_COMPONENT_MAX_INFLOWS = 10
_EXACT_COMPONENT_MAX_PAIRS = 60
_MATCHING_SCORE_EPSILON = 1e-9


def _build_pair_components(
    pairs: Sequence[ScoredTransferPair],
) -> list[list[ScoredTransferPair]]:
    by_out: dict[str, list[ScoredTransferPair]] = {}
    by_in: dict[str, list[ScoredTransferPair]] = {}
    for pair in pairs:
        by_out.setdefault(pair.transaction_out_id, []).append(pair)
        by_in.setdefault(pair.transaction_in_id, []).append(pair)

    visited: set[tuple[str, str]] = set()
    components: list[list[ScoredTransferPair]] = []
    for pair in pairs:
        pair_key = (pair.transaction_out_id, pair.transaction_in_id)
        if pair_key in visited:
            continue

        queue = [pair]
        component: list[ScoredTransferPair] = []
        seen_out: set[str] = set()
        seen_in: set[str] = set()
        while queue:
            candidate = queue.pop()
            candidate_key = (candidate.transaction_out_id, candidate.transaction_in_id)
            if candidate_key in visited:
                continue
            visited.add(candidate_key)
            component.append(candidate)

            if candidate.transaction_out_id not in seen_out:
                seen_out.add(candidate.transaction_out_id)
                queue.extend(by_out.get(candidate.transaction_out_id, []))
            if candidate.transaction_in_id not in seen_in:
                seen_in.add(candidate.transaction_in_id)
                queue.extend(by_in.get(candidate.transaction_in_id, []))

        components.append(component)

    return components


def _is_better_matching(
    candidate: tuple[float, int, tuple[int, ...]],
    current: tuple[float, int, tuple[int, ...]],
) -> bool:
    candidate_score, candidate_count, candidate_ranks = candidate
    current_score, current_count, current_ranks = current

    if candidate_score > current_score + _MATCHING_SCORE_EPSILON:
        return True
    if candidate_score + _MATCHING_SCORE_EPSILON < current_score:
        return False
    if candidate_count > current_count:
        return True
    if candidate_count < current_count:
        return False
    return candidate_ranks < current_ranks


def _select_component_pairs_exact(
    pairs: Sequence[ScoredTransferPair],
    *,
    rank_key,
) -> list[ScoredTransferPair]:
    from functools import lru_cache

    ranked_pairs = sorted(pairs, key=rank_key)
    rank_index_by_pair = {
        (pair.transaction_out_id, pair.transaction_in_id): index
        for index, pair in enumerate(ranked_pairs)
    }

    out_ids = sorted({pair.transaction_out_id for pair in pairs})
    in_ids = sorted({pair.transaction_in_id for pair in pairs})
    in_index = {in_id: index for index, in_id in enumerate(in_ids)}
    out_edges: dict[str, list[ScoredTransferPair]] = {}
    for pair in pairs:
        out_edges.setdefault(pair.transaction_out_id, []).append(pair)
    for out_id in out_edges:
        out_edges[out_id].sort(key=rank_key)

    @lru_cache(maxsize=None)
    def search(out_pos: int, used_in_mask: int) -> tuple[float, int, tuple[int, ...]]:
        if out_pos >= len(out_ids):
            return (0.0, 0, tuple())

        best = search(out_pos + 1, used_in_mask)
        out_id = out_ids[out_pos]
        for pair in out_edges.get(out_id, []):
            in_pos = in_index[pair.transaction_in_id]
            bit = 1 << in_pos
            if used_in_mask & bit:
                continue
            tail_score, tail_count, tail_ranks = search(out_pos + 1, used_in_mask | bit)
            pair_rank = rank_index_by_pair[(pair.transaction_out_id, pair.transaction_in_id)]
            candidate = (
                tail_score + pair.score,
                tail_count + 1,
                tuple(sorted((pair_rank, *tail_ranks))),
            )
            if _is_better_matching(candidate, best):
                best = candidate
        return best

    _, _, selected_rank_indices = search(0, 0)
    return [ranked_pairs[index] for index in selected_rank_indices]


def _select_component_pairs_greedy(
    pairs: Sequence[ScoredTransferPair],
    *,
    rank_key,
) -> list[ScoredTransferPair]:
    selected: list[ScoredTransferPair] = []
    used_out: set[str] = set()
    used_in: set[str] = set()
    for pair in sorted(pairs, key=rank_key):
        if pair.transaction_out_id in used_out or pair.transaction_in_id in used_in:
            continue
        selected.append(pair)
        used_out.add(pair.transaction_out_id)
        used_in.add(pair.transaction_in_id)
    return selected


def select_transfer_links(
    scored_pairs: Sequence[ScoredTransferPair],
    *,
    suggested_threshold: float = 0.80,
    auto_threshold: float = 0.92,
    ambiguity_margin: float = 0.05,
    recurrent_lane_min_score: float = 0.84,
    recurrent_lane_min_count: int = 15,
    one_sided_lane_min_score: float = 0.74,
    one_sided_lane_min_count: int = 45,
    lane_prior_min_confirmations: int = 3,
    lane_prior_auto_min_score: float = 0.86,
) -> TransferSelection:
    def is_one_sided_known_lane_candidate(pair: ScoredTransferPair) -> bool:
        if pair.score < one_sided_lane_min_score:
            return False
        if pair.amount_delta_cents != 0:
            return False
        if pair.time_delta_seconds > 24 * 60 * 60:
            return False
        if pair.out_hint == pair.in_hint:
            return False
        if pair.out_hint and not pair.in_hint:
            return pair.in_known_internal_lane_marker
        if pair.in_hint and not pair.out_hint:
            return pair.out_known_internal_lane_marker
        return False

    viable = [
        pair
        for pair in scored_pairs
        if pair.score >= suggested_threshold or is_one_sided_known_lane_candidate(pair)
    ]
    account_pair_forward_counts = _build_account_pair_forward_counts(viable)
    lane_population = [pair for pair in scored_pairs if pair.score >= one_sided_lane_min_score]
    account_pair_total_counts = _build_account_pair_total_counts(lane_population)

    def rank_key(pair: ScoredTransferPair) -> tuple[float | int | str, ...]:
        return _pair_rank_key(
            pair,
            account_pair_forward_counts=account_pair_forward_counts,
        )

    viable.sort(key=rank_key)

    best_by_out: dict[str, list[ScoredTransferPair]] = {}
    best_by_in: dict[str, list[ScoredTransferPair]] = {}
    pair_lookup: dict[tuple[str, str], ScoredTransferPair] = {}
    for pair in viable:
        best_by_out.setdefault(pair.transaction_out_id, []).append(pair)
        best_by_in.setdefault(pair.transaction_in_id, []).append(pair)
        pair_lookup[(pair.transaction_out_id, pair.transaction_in_id)] = pair
    for pairs in best_by_out.values():
        pairs.sort(key=rank_key)
    for pairs in best_by_in.values():
        pairs.sort(key=rank_key)

    auto_links: list[SelectedTransferLink] = []
    suggested_links: list[SelectedTransferLink] = []
    selected_pairs: list[tuple[ScoredTransferPair, str, int, int, int]] = []
    components = _build_pair_components(viable)
    components.sort(key=lambda component: rank_key(min(component, key=rank_key)))
    for component in components:
        component_out_count = len({pair.transaction_out_id for pair in component})
        component_in_count = len({pair.transaction_in_id for pair in component})
        use_exact = (
            component_out_count <= _EXACT_COMPONENT_MAX_OUTFLOWS
            and component_in_count <= _EXACT_COMPONENT_MAX_INFLOWS
            and len(component) <= _EXACT_COMPONENT_MAX_PAIRS
        )
        mode = "exact" if use_exact else "fallback"
        if use_exact:
            matched = _select_component_pairs_exact(component, rank_key=rank_key)
        else:
            matched = _select_component_pairs_greedy(component, rank_key=rank_key)
        for pair in matched:
            selected_pairs.append(
                (
                    pair,
                    mode,
                    component_out_count,
                    component_in_count,
                    len(component),
                )
            )

    selected_pairs.sort(key=lambda item: rank_key(item[0]))
    used_ids: set[str] = set()

    for (
        pair,
        component_mode,
        component_out_count,
        component_in_count,
        component_pair_count,
    ) in selected_pairs:
        if pair.transaction_out_id in used_ids or pair.transaction_in_id in used_ids:
            continue

        out_pairs = [
            candidate
            for candidate in best_by_out.get(pair.transaction_out_id, [])
            if candidate.transaction_in_id not in used_ids
        ]
        in_pairs = [
            candidate
            for candidate in best_by_in.get(pair.transaction_in_id, [])
            if candidate.transaction_out_id not in used_ids
        ]
        pair_is_local_top = (
            bool(out_pairs) and bool(in_pairs) and out_pairs[0] == pair and in_pairs[0] == pair
        )
        if pair_is_local_top:
            out_ambiguous_all, out_tiebreak = _is_ambiguous_candidate_set(
                out_pairs,
                ambiguity_margin=ambiguity_margin,
                account_pair_forward_counts=account_pair_forward_counts,
            )
            in_ambiguous_all, in_tiebreak = _is_ambiguous_candidate_set(
                in_pairs,
                ambiguity_margin=ambiguity_margin,
                account_pair_forward_counts=account_pair_forward_counts,
            )
            out_ambiguous_for_auto, _ = _is_ambiguous_candidate_set(
                out_pairs,
                ambiguity_margin=ambiguity_margin,
                focus_threshold=auto_threshold,
                account_pair_forward_counts=account_pair_forward_counts,
            )
            in_ambiguous_for_auto, _ = _is_ambiguous_candidate_set(
                in_pairs,
                ambiguity_margin=ambiguity_margin,
                focus_threshold=auto_threshold,
                account_pair_forward_counts=account_pair_forward_counts,
            )
            ambiguous = out_ambiguous_for_auto or in_ambiguous_for_auto
            auto_override_enabled, auto_override_reason = _resolve_two_by_two_conflict(
                pair=pair,
                out_pairs=out_pairs,
                in_pairs=in_pairs,
                pair_lookup=pair_lookup,
                auto_threshold=auto_threshold,
            )
            if ambiguous and auto_override_enabled:
                ambiguous = False
        else:
            # Global matching may pick a non-local-best edge to maximize component total score.
            # Keep such edges conservative unless local ambiguity checks clear them.
            out_ambiguous_all = True
            in_ambiguous_all = True
            ambiguous = True
            auto_override_enabled = False
            auto_override_reason = "none"
            out_tiebreak = "global_component"
            in_tiebreak = "global_component"

        pair_key = _account_pair_key(pair)
        pair_total_count = account_pair_total_counts.get(pair_key, 0) if pair_key is not None else 0
        recurrent_lane_auto = (
            pair.score >= recurrent_lane_min_score
            and pair.score < auto_threshold
            and pair.amount_delta_cents == 0
            and pair.out_hint
            and pair.in_hint
            and pair.time_delta_seconds <= 24 * 60 * 60
            and pair_total_count >= recurrent_lane_min_count
        )
        one_sided_lane_auto = (
            pair.score < auto_threshold
            and pair_total_count >= one_sided_lane_min_count
            and not out_ambiguous_all
            and not in_ambiguous_all
            and is_one_sided_known_lane_candidate(pair)
        )
        lane_prior_auto = (
            pair.score >= lane_prior_auto_min_score
            and pair.score < auto_threshold
            and pair.amount_delta_cents == 0
            and pair.lane_prior_confirmations >= lane_prior_min_confirmations
            and pair.time_delta_seconds <= max(24 * 60 * 60, pair.lane_prior_window_seconds or 0.0)
            and (pair.lane_prior_pattern_hits_out > 0 or pair.out_hint)
            and (pair.lane_prior_pattern_hits_in > 0 or pair.in_hint)
        )
        generic_outflow_guard = (
            pair.outflow_requires_stronger_evidence
            and pair.in_hint
            and not pair.bank_reference_match
        )

        status = "suggested"
        if (
            (
                (pair.score >= auto_threshold and (pair.out_hint or pair.in_hint))
                or recurrent_lane_auto
                or one_sided_lane_auto
                or lane_prior_auto
            )
            and not ambiguous
            and not generic_outflow_guard
        ):
            status = "auto"

        if status != "auto" and pair.score < suggested_threshold:
            continue

        lane_mode = "none"
        if lane_prior_auto:
            lane_mode = "lane_prior"
        elif recurrent_lane_auto:
            lane_mode = "recurrent"
        elif one_sided_lane_auto:
            lane_mode = "one_sided_recurrent"

        link = SelectedTransferLink(
            transaction_out_id=pair.transaction_out_id,
            transaction_in_id=pair.transaction_in_id,
            status=status,
            score=pair.score,
            rationale=(
                pair.rationale
                + ("; ambiguous=1" if ambiguous else "; ambiguous=0")
                + ("; auto_guard=generic_outflow" if generic_outflow_guard else "; auto_guard=none")
                + (
                    "; auto_override=" + auto_override_reason
                    if auto_override_enabled
                    else "; auto_override=none"
                )
                + (
                    "; auto_lane=lane_prior"
                    if lane_mode == "lane_prior"
                    else (
                        "; auto_lane=recurrent"
                        if lane_mode == "recurrent"
                        else (
                            "; auto_lane=one_sided_recurrent"
                            if lane_mode == "one_sided_recurrent"
                            else "; auto_lane=none"
                        )
                    )
                )
                + (
                    f"; lane_count={pair.lane_prior_confirmations}"
                    if lane_mode == "lane_prior"
                    else ""
                )
                + (
                    f"; lane_count={pair_total_count}"
                    if lane_mode in {"recurrent", "one_sided_recurrent"}
                    else ""
                )
                + f"; tiebreak_out={out_tiebreak}; tiebreak_in={in_tiebreak}"
                + (
                    f"; component_mode={component_mode}; component_size={component_out_count}x{component_in_count}/{component_pair_count}"
                )
            ),
            fee_amount=pair.fee_amount,
        )
        if status == "auto":
            auto_links.append(link)
        else:
            suggested_links.append(link)
        used_ids.add(pair.transaction_out_id)
        used_ids.add(pair.transaction_in_id)

    return TransferSelection(auto_links=auto_links, suggested_links=suggested_links)
