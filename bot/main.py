from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000/api")
API_ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN", "")
BOT_LOG_LEVEL = (os.getenv("BOT_LOG_LEVEL") or "INFO").upper()
MEDIA_GROUP_DEBOUNCE_SECONDS = float(os.getenv("MEDIA_GROUP_DEBOUNCE_SECONDS", "1.0"))
RETRY_CACHE_TTL_SECONDS = int(os.getenv("RETRY_CACHE_TTL_SECONDS", "3600"))
RETRY_CACHE_MAX_ITEMS = int(os.getenv("RETRY_CACHE_MAX_ITEMS", "200"))
IMPORT_STATUS_POLL_SECONDS = float(os.getenv("IMPORT_STATUS_POLL_SECONDS", "2.0"))
IMPORT_STATUS_POLL_MAX_SECONDS = float(os.getenv("IMPORT_STATUS_POLL_MAX_SECONDS", "180.0"))
TRANSFER_QUEUE_COUNT_LIMIT = 500
API_RETRY_MAX_ATTEMPTS = int(os.getenv("API_RETRY_MAX_ATTEMPTS", "3"))
API_RETRY_INITIAL_BACKOFF_SECONDS = float(os.getenv("API_RETRY_INITIAL_BACKOFF_SECONDS", "0.5"))
API_RETRY_MAX_BACKOFF_SECONDS = float(os.getenv("API_RETRY_MAX_BACKOFF_SECONDS", "4.0"))
IMPORT_STATUS_POLL_MAX_ERRORS = int(os.getenv("IMPORT_STATUS_POLL_MAX_ERRORS", "3"))

logger = logging.getLogger(__name__)


def _parse_owner_id(value: str) -> int:
    try:
        return int((value or "").strip() or "0")
    except ValueError:
        return 0


OWNER_TELEGRAM_ID = _parse_owner_id(os.getenv("OWNER_TELEGRAM_ID", ""))


def _configure_logging() -> None:
    level = getattr(logging, BOT_LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _sanitize_api_base_url(raw_value: str) -> str:
    raw = (raw_value or "").strip()
    if not raw:
        raise ValueError("API_BASE_URL is required.")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("API_BASE_URL must start with http:// or https://.")
    if not parsed.netloc:
        raise ValueError("API_BASE_URL must include a host.")
    return raw.rstrip("/")


API_BASE_URL = _sanitize_api_base_url(API_BASE_URL)


@dataclass
class PendingUpload:
    file_name: str
    file_bytes: bytes
    message_id: Optional[int] = None
    status_message_id: Optional[int] = None


@dataclass
class RetryCacheItem:
    created_at: float
    source: str
    upload: PendingUpload


_media_group_lock = asyncio.Lock()
_media_group_pending: Dict[Tuple[int, str], List[PendingUpload]] = {}
_media_group_tasks: Dict[Tuple[int, str], asyncio.Task] = {}
_retry_cache: Dict[str, RetryCacheItem] = {}
_batch_status_tasks: Dict[str, asyncio.Task] = {}


def _prune_retry_cache(now: Optional[float] = None) -> None:
    if now is None:
        now = time.time()

    expired = [
        token
        for token, item in _retry_cache.items()
        if now - item.created_at > RETRY_CACHE_TTL_SECONDS
    ]
    for token in expired:
        _retry_cache.pop(token, None)

    if len(_retry_cache) <= RETRY_CACHE_MAX_ITEMS:
        return

    overflow = len(_retry_cache) - RETRY_CACHE_MAX_ITEMS
    for token, _ in sorted(_retry_cache.items(), key=lambda kv: kv[1].created_at)[:overflow]:
        _retry_cache.pop(token, None)


def _is_owner(update: Update) -> bool:
    if not OWNER_TELEGRAM_ID:
        return True
    user = update.effective_user
    if not user:
        return False
    return user.id == OWNER_TELEGRAM_ID


def _cache_retry_upload(*, source: str, upload: PendingUpload) -> str:
    _prune_retry_cache()
    token = uuid.uuid4().hex
    _retry_cache[token] = RetryCacheItem(created_at=time.time(), source=source, upload=upload)
    return token


def _build_status_keyboard(*, include_retry: bool, retry_token: Optional[str] = None) -> Optional[InlineKeyboardMarkup]:
    rows: List[List[InlineKeyboardButton]] = []
    if include_retry:
        if not retry_token:
            raise ValueError("retry_token is required when include_retry=True")
        rows.append([InlineKeyboardButton("Retry upload", callback_data=f"retry:{retry_token}")])
    rows.append([InlineKeyboardButton("Dismiss", callback_data="dismiss")])
    return InlineKeyboardMarkup(rows) if rows else None


def _truncate_label(value: str, max_len: int = 48) -> str:
    v = value or ""
    if len(v) <= max_len:
        return v
    return v[: max(0, max_len - 1)] + "…"


def _build_clean_menu_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Delete last upload", callback_data="clean:delete_last")],
        [InlineKeyboardButton("Choose file to delete…", callback_data="clean:list:0")],
        [InlineKeyboardButton("Delete ALL data…", callback_data="clean:purge_warn")],
    ]
    rows.append([InlineKeyboardButton("Dismiss", callback_data="dismiss")])
    return InlineKeyboardMarkup(rows)


def _build_clean_list_keyboard(files: List[Dict[str, Any]], *, offset: int, limit: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for item in files:
        file_id = (item or {}).get("id") or ""
        if not file_id:
            continue
        name = (item or {}).get("file_name") or "unknown"
        status = (item or {}).get("status") or "unknown"
        label = _truncate_label(f"{name} ({status})")
        rows.append([InlineKeyboardButton(f"Delete: {label}", callback_data=f"clean:ask_delete:{file_id}")])

    nav: List[InlineKeyboardButton] = []
    if offset > 0:
        nav.append(InlineKeyboardButton("Prev", callback_data=f"clean:list:{max(0, offset - limit)}"))
    if len(files) >= limit:
        nav.append(InlineKeyboardButton("Next", callback_data=f"clean:list:{offset + limit}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("Back", callback_data="clean:menu")])
    rows.append([InlineKeyboardButton("Dismiss", callback_data="dismiss")])
    return InlineKeyboardMarkup(rows)


def _build_clean_confirm_delete_keyboard(file_id: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Confirm delete", callback_data=f"clean:confirm_delete:{file_id}")],
        [InlineKeyboardButton("Cancel", callback_data="clean:menu")],
    ]
    rows.append([InlineKeyboardButton("Dismiss", callback_data="dismiss")])
    return InlineKeyboardMarkup(rows)


def _build_clean_purge_warn_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Continue", callback_data="clean:purge_confirm")],
        [InlineKeyboardButton("Cancel", callback_data="clean:menu")],
    ]
    rows.append([InlineKeyboardButton("Dismiss", callback_data="dismiss")])
    return InlineKeyboardMarkup(rows)


def _build_clean_purge_confirm_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("DELETE ALL", callback_data="clean:purge_execute")],
        [InlineKeyboardButton("Cancel", callback_data="clean:menu")],
    ]
    rows.append([InlineKeyboardButton("Dismiss", callback_data="dismiss")])
    return InlineKeyboardMarkup(rows)




def _build_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if API_ADMIN_TOKEN:
        headers["x-admin-token"] = API_ADMIN_TOKEN
    return headers


def _compute_backoff_seconds(attempt: int) -> float:
    if attempt <= 1:
        return 0.0
    factor = 2 ** (attempt - 2)
    base = API_RETRY_INITIAL_BACKOFF_SECONDS * factor
    jitter = random.uniform(0.0, max(0.0, base * 0.25))
    return min(API_RETRY_MAX_BACKOFF_SECONDS, base + jitter)


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        return bool(response is not None and _is_retryable_status(response.status_code))
    if isinstance(exc, requests.RequestException):
        return True
    return False


async def _request_with_retries(
    *,
    method: str,
    path: str,
    timeout: int,
    headers: Optional[Dict[str, str]] = None,
    files: Any = None,
    json_body: Any = None,
    max_attempts: int = API_RETRY_MAX_ATTEMPTS,
) -> requests.Response:
    url = f"{API_BASE_URL}{path}"
    request_headers = headers or _build_headers()
    attempts = max(1, max_attempts)

    for attempt in range(1, attempts + 1):
        try:
            response = await asyncio.to_thread(
                requests.request,
                method,
                url,
                headers=request_headers,
                timeout=timeout,
                files=files,
                json=json_body,
                allow_redirects=False,
            )
            if response.status_code >= 400:
                if attempt < attempts and _is_retryable_status(response.status_code):
                    delay = _compute_backoff_seconds(attempt + 1)
                    logger.warning(
                        "Transient API status %s for %s %s (attempt %d/%d); retrying in %.2fs.",
                        response.status_code,
                        method,
                        path,
                        attempt,
                        attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
            return response
        except Exception as exc:
            if attempt < attempts and _is_retryable_exception(exc):
                delay = _compute_backoff_seconds(attempt + 1)
                logger.warning(
                    "Transient API error for %s %s (attempt %d/%d): %s. Retrying in %.2fs.",
                    method,
                    path,
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise


def _format_status_label(status: str) -> str:
    label = (status or "").strip().lower()
    if label == "processed":
        return "parsed"
    if label == "received":
        return "uploaded"
    if label == "parsed":
        return "parsed"
    return label or "unknown"


def _format_status_with_emoji(status: str) -> str:
    normalized = (status or "").strip().lower()
    emoji_map = {
        "received": "📥",
        "queued": "🕓",
        "processing": "⏳",
        "processed": "✅",
        "parsed": "✅",
        "failed": "❌",
        "duplicate": "♻️",
    }
    emoji = emoji_map.get(normalized, "❔")
    label = _format_status_label(normalized)
    return f"{emoji} {label}"


def _format_file_result(batch_id: str, file_info: Dict[str, Any]) -> str:
    file_name = file_info.get("file_name") or "unknown.pdf"
    status = file_info.get("status") or "unknown"
    status_label = _format_status_with_emoji(status)
    error_message = file_info.get("error_message") or ""
    file_id = file_info.get("id") or ""
    file_hash = file_info.get("file_hash") or ""

    actions: List[str] = []
    if status == "received":
        actions.append("Upload accepted; waiting to be queued for processing.")
    elif status == "queued":
        actions.append("Saved and queued for import processing.")
    elif status == "processing":
        actions.append("Parsing PDF and writing statements/transactions to the database.")
    elif status == "processed":
        actions.append("Parsed successfully.")
        actions.append("Statements and transactions added to the database.")
    elif status == "duplicate":
        actions.append("Duplicate detected (by content hash); not queued.")
    elif status == "failed":
        err_lower = (error_message or "").lower()
        if "not a pdf" in err_lower:
            actions.append("Rejected; not queued.")
        else:
            actions.append("Import failed during parsing.")
        if error_message:
            actions.append(f"Reason: {error_message}")
        if "parsing" in err_lower or "pdf" in err_lower:
            actions.append("An exception may have been created for review.")
    else:
        actions.append("Recorded with the status above.")

    actions_block = "\n".join(f"- {line}" for line in actions) if actions else "- (none)"

    lines = [
        f"File: {file_name}",
        f"Status: {status_label}",
        f"Batch id: {batch_id}",
    ]
    if file_id:
        lines.append(f"Import file id: {file_id}")
    if file_hash:
        lines.append(f"File hash: {file_hash}")
    lines.append("Actions:")
    lines.append(actions_block)
    return "\n".join(lines)


def _format_received_ack(file_name: str, file_size_bytes: int) -> str:
    kib = max(1, int(round(file_size_bytes / 1024)))
    return "\n".join(
        [
            f"File: {file_name}",
            f"Status: {_format_status_with_emoji('received')}",
            "Actions:",
            f"- Downloaded from Telegram ({kib} KiB).",
            "- Uploading to import queue...",
        ]
    )


async def _edit_or_send_status_message(
    *,
    chat_id: int,
    message_id: Optional[int],
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup],
    context: ContextTypes.DEFAULT_TYPE,
    reply_to_message_id: Optional[int] = None,
) -> int:
    if message_id is not None:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
            )
            return message_id
        except Exception:
            pass

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        reply_to_message_id=reply_to_message_id,
    )
    return sent.message_id


def _format_amount(amount: Any, direction: str = "") -> str:
    try:
        value = float(amount)
    except Exception:
        return "—"
    sign = "+" if direction == "in" else "-" if direction == "out" else ""
    return f"{sign}{value:,.2f}".replace(",", " ")


def _format_datetime(value: Any) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _format_queue_count(total: int, *, capped: bool = False) -> str:
    safe_total = max(0, int(total))
    if capped and safe_total > 0:
        return f"{safe_total}+"
    return str(safe_total)


def _suggested_category_from_exception(item: Dict[str, Any]) -> str:
    payload = item.get("payload")
    if not isinstance(payload, dict):
        return ""

    for key in ("suggested_category", "category", "after_category"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    suggested = payload.get("suggested")
    if isinstance(suggested, dict):
        value = suggested.get("category")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_duplicate_candidate_exception(item: Dict[str, Any]) -> bool:
    exception_type = str(item.get("exception_type") or "").lower()
    if "duplicate" in exception_type:
        return True

    payload = item.get("payload")
    if not isinstance(payload, dict):
        return False

    for key in ("duplicate", "is_duplicate", "suspected_duplicate"):
        value = payload.get(key)
        if value is True:
            return True
    return False


def _build_queue_keyboard(
    *,
    kind: str,
    item_id: str,
    exception: Optional[Dict[str, Any]] = None,
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if kind == "transfer":
        rows.append(
            [
                InlineKeyboardButton("Confirm transfer", callback_data=f"queue:transfer:confirm:{item_id}"),
                InlineKeyboardButton("Reject transfer", callback_data=f"queue:transfer:reject:{item_id}"),
            ]
        )
    elif kind == "exception":
        suggested_category = _suggested_category_from_exception(exception or {})
        if suggested_category:
            rows.append(
                [InlineKeyboardButton("Approve category", callback_data=f"queue:exc:ac:{item_id}")]
            )

        entity_type = str((exception or {}).get("entity_type") or "")
        if entity_type == "transaction" or _is_duplicate_candidate_exception(exception or {}):
            rows.append(
                [InlineKeyboardButton("Mark duplicate", callback_data=f"queue:exc:dup:{item_id}")]
            )

        rows.append(
            [InlineKeyboardButton("Resolve exception", callback_data=f"queue:exception:resolve:{item_id}")]
        )

    rows.append([InlineKeyboardButton("Next", callback_data="queue:next")])
    rows.append([InlineKeyboardButton("Dismiss", callback_data="dismiss")])
    return InlineKeyboardMarkup(rows)


async def _get_next_queue_item() -> Optional[Dict[str, Any]]:
    transfers = await _get_transfer_links(status="suggested", limit=TRANSFER_QUEUE_COUNT_LIMIT)
    if transfers:
        link = transfers[0]
        out_id = link.get("transaction_out_id") or ""
        in_id = link.get("transaction_in_id") or ""
        out_tx = await _get_transaction(transaction_id=out_id) if out_id else {}
        in_tx = await _get_transaction(transaction_id=in_id) if in_id else {}
        return {
            "kind": "transfer",
            "link": link,
            "out_tx": out_tx,
            "in_tx": in_tx,
            "queue_total": len(transfers),
            "queue_total_capped": len(transfers) >= TRANSFER_QUEUE_COUNT_LIMIT,
        }

    exceptions = await _get_exceptions()
    open_exceptions = [item for item in exceptions if (item or {}).get("status") == "open"]
    if open_exceptions:
        return {"kind": "exception", "exception": open_exceptions[0], "queue_total": len(open_exceptions)}
    return None


def _format_transfer_queue_item(
    link: Dict[str, Any],
    out_tx: Dict[str, Any],
    in_tx: Dict[str, Any],
    *,
    queue_total: Optional[int] = None,
    queue_total_capped: bool = False,
) -> str:
    score = link.get("match_score")
    rationale = link.get("rationale") or ""
    status = link.get("status") or "suggested"

    lines = [
        "Transfer review",
        f"Status: {status}",
    ]
    if queue_total is not None:
        lines.append(
            f"Remaining suggested transfers: {_format_queue_count(queue_total, capped=queue_total_capped)}"
        )
    lines.extend(
        [
            f"Score: {score:.2f}" if isinstance(score, (int, float)) else f"Score: {score or '—'}",
        ]
    )

    if rationale:
        lines.append(f"Reason: {rationale}")

    lines.append("")
    lines.append("Outflow:")
    lines.append(
        f"- {_format_datetime(out_tx.get('operation_datetime') or out_tx.get('posting_datetime'))} · "
        f"{_format_amount(out_tx.get('amount'), 'out')} · {out_tx.get('description_raw') or out_tx.get('merchant_normalized') or '—'}"
    )
    lines.append("Inflow:")
    lines.append(
        f"- {_format_datetime(in_tx.get('operation_datetime') or in_tx.get('posting_datetime'))} · "
        f"{_format_amount(in_tx.get('amount'), 'in')} · {in_tx.get('description_raw') or in_tx.get('merchant_normalized') or '—'}"
    )
    return "\n".join(lines)


def _format_exception_queue_item(item: Dict[str, Any], *, queue_total: Optional[int] = None) -> str:
    payload = item.get("payload") or {}
    payload_summary = ""
    if payload:
        payload_summary = str(payload)
        if len(payload_summary) > 180:
            payload_summary = payload_summary[:180] + "…"

    lines = [
        "Exception review",
        f"Type: {item.get('exception_type') or 'unknown'}",
        f"Severity: {item.get('severity') or 'medium'}",
        f"Status: {item.get('status') or 'open'}",
        f"Entity: {item.get('entity_type') or 'unknown'} · {item.get('entity_id') or '—'}",
    ]
    if queue_total is not None:
        lines.append(f"Remaining open exceptions: {_format_queue_count(queue_total)}")
    rationale = item.get("rationale") or ""
    if rationale:
        lines.append(f"Rationale: {rationale}")
    suggested_category = _suggested_category_from_exception(item)
    if suggested_category:
        lines.append(f"Suggested category: {suggested_category}")
    if _is_duplicate_candidate_exception(item):
        lines.append("Duplicate candidate: yes")
    if payload_summary:
        lines.append(f"Payload: {payload_summary}")
    return "\n".join(lines)


async def _send_next_queue_item(
    *,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    message_id: Optional[int] = None,
) -> None:
    try:
        item = await _get_next_queue_item()
    except Exception as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"Failed to load inbox: {exc}")
        return

    if not item:
        text = "Inbox is empty. No exceptions or transfer suggestions."
        if message_id is not None:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
                return
            except Exception:
                pass
        await context.bot.send_message(chat_id=chat_id, text=text)
        return

    if item.get("kind") == "transfer":
        link = item.get("link") or {}
        out_tx = item.get("out_tx") or {}
        in_tx = item.get("in_tx") or {}
        text = _format_transfer_queue_item(
            link,
            out_tx,
            in_tx,
            queue_total=item.get("queue_total"),
            queue_total_capped=bool(item.get("queue_total_capped")),
        )
        keyboard = _build_queue_keyboard(kind="transfer", item_id=link.get("id") or "")
    else:
        exc = item.get("exception") or {}
        text = _format_exception_queue_item(exc, queue_total=item.get("queue_total"))
        keyboard = _build_queue_keyboard(kind="exception", item_id=exc.get("id") or "", exception=exc)

    if message_id is not None:
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard)
            return
        except Exception:
            pass

    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)


async def _post_import_files(source: str, uploads: List[PendingUpload]) -> Dict[str, Any]:
    files = [("files", (u.file_name, u.file_bytes, "application/pdf")) for u in uploads]
    logger.info("Uploading %d file(s) to import queue (source=%s).", len(uploads), source)
    response = await _request_with_retries(
        method="POST",
        path=f"/imports/pdf?source={source}",
        files=files,
        timeout=120,
        max_attempts=API_RETRY_MAX_ATTEMPTS,
    )
    return response.json()


async def _get_import_files(*, limit: int, offset: int) -> List[Dict[str, Any]]:
    response = await _request_with_retries(
        method="GET",
        path=f"/imports/files?limit={limit}&offset={offset}",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, list) else []


async def _get_import_batch(*, batch_id: str) -> Dict[str, Any]:
    response = await _request_with_retries(
        method="GET",
        path=f"/imports/batches/{batch_id}",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _delete_import_file(*, file_id: str) -> Dict[str, Any]:
    response = await _request_with_retries(
        method="DELETE",
        path=f"/imports/files/{file_id}",
        timeout=120,
        max_attempts=1,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _purge_data() -> Dict[str, Any]:
    response = await _request_with_retries(
        method="POST",
        path="/imports/purge",
        json_body={"confirm": "delete-all"},
        timeout=180,
        max_attempts=1,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _get_exceptions() -> List[Dict[str, Any]]:
    response = await _request_with_retries(
        method="GET",
        path="/exceptions/",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, list) else []


async def _resolve_exception(*, exception_id: str) -> Dict[str, Any]:
    response = await _request_with_retries(
        method="POST",
        path=f"/exceptions/{exception_id}/resolve",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _approve_exception_category(*, exception_id: str) -> Dict[str, Any]:
    response = await _request_with_retries(
        method="POST",
        path=f"/exceptions/{exception_id}/approve-category",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _mark_exception_duplicate(*, exception_id: str) -> Dict[str, Any]:
    response = await _request_with_retries(
        method="POST",
        path=f"/exceptions/{exception_id}/mark-duplicate",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _get_transfer_links(*, status: str = "suggested", limit: int = 50) -> List[Dict[str, Any]]:
    response = await _request_with_retries(
        method="GET",
        path=f"/transfers/links?status={status}&limit={limit}",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, list) else []


async def _confirm_transfer_link(*, link_id: str) -> Dict[str, Any]:
    response = await _request_with_retries(
        method="POST",
        path=f"/transfers/links/{link_id}/confirm",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _reject_transfer_link(*, link_id: str) -> Dict[str, Any]:
    response = await _request_with_retries(
        method="POST",
        path=f"/transfers/links/{link_id}/reject",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _get_transaction(*, transaction_id: str) -> Dict[str, Any]:
    response = await _request_with_retries(
        method="GET",
        path=f"/transactions/{transaction_id}",
        timeout=60,
    )
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


async def _send_per_file_status_messages(
    *,
    chat_id: int,
    uploads: List[PendingUpload],
    batch_payload: Dict[str, Any],
    context: ContextTypes.DEFAULT_TYPE,
) -> Dict[str, int]:
    batch_id = batch_payload.get("id") or ""
    files_info = batch_payload.get("files") or []

    # Best-effort mapping to reply to the original message for each file.
    message_ids_by_name: Dict[str, List[int]] = {}
    for upload in uploads:
        if upload.message_id is None:
            continue
        message_ids_by_name.setdefault(upload.file_name, []).append(upload.message_id)

    status_message_ids_by_name: Dict[str, List[int]] = {}
    for upload in uploads:
        if upload.status_message_id is None:
            continue
        status_message_ids_by_name.setdefault(upload.file_name, []).append(upload.status_message_id)

    message_ids_by_file_id: Dict[str, int] = {}

    for file_info in files_info:
        name = (file_info or {}).get("file_name") or ""
        file_id = (file_info or {}).get("id") or ""
        text = _format_file_result(batch_id=batch_id, file_info=file_info)
        reply_markup = _build_status_keyboard(include_retry=False)

        # Prefer editing the per-file “received” message if we have it, otherwise send a new one.
        target_status_message_id: Optional[int] = None
        if name in status_message_ids_by_name and status_message_ids_by_name[name]:
            target_status_message_id = status_message_ids_by_name[name].pop(0)

        reply_to_message_id: Optional[int] = None
        if name in message_ids_by_name and message_ids_by_name[name]:
            reply_to_message_id = message_ids_by_name[name].pop(0)

        message_id = await _edit_or_send_status_message(
            chat_id=chat_id,
            message_id=target_status_message_id,
            text=text,
            reply_markup=reply_markup,
            context=context,
            reply_to_message_id=reply_to_message_id,
        )

        if file_id:
            message_ids_by_file_id[file_id] = message_id

    return message_ids_by_file_id


def _should_poll_status(status: str) -> bool:
    return (status or "") in {"queued", "processing", "received"}


async def _poll_import_batch_statuses(
    *,
    chat_id: int,
    batch_id: str,
    message_ids_by_file_id: Dict[str, int],
    initial_status_by_file_id: Dict[str, str],
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if IMPORT_STATUS_POLL_SECONDS <= 0 or IMPORT_STATUS_POLL_MAX_SECONDS <= 0:
        return

    deadline = time.monotonic() + IMPORT_STATUS_POLL_MAX_SECONDS
    last_status_by_file_id: Dict[str, str] = dict(initial_status_by_file_id)
    consecutive_errors = 0

    try:
        while time.monotonic() < deadline:
            try:
                batch_payload = await _get_import_batch(batch_id=batch_id)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                logger.warning(
                    "Import batch polling failed for batch '%s' (%d/%d): %s",
                    batch_id,
                    consecutive_errors,
                    IMPORT_STATUS_POLL_MAX_ERRORS,
                    exc,
                )
                if consecutive_errors >= IMPORT_STATUS_POLL_MAX_ERRORS:
                    break
                await asyncio.sleep(IMPORT_STATUS_POLL_SECONDS)
                continue

            files_info = batch_payload.get("files") or []
            pending = False

            for file_info in files_info:
                file_id = (file_info or {}).get("id") or ""
                if not file_id or file_id not in message_ids_by_file_id:
                    continue

                status = (file_info or {}).get("status") or "unknown"
                if _should_poll_status(status):
                    pending = True

                if last_status_by_file_id.get(file_id) == status:
                    continue

                text = _format_file_result(batch_id=batch_id, file_info=file_info)
                reply_markup = _build_status_keyboard(include_retry=False)
                message_id = await _edit_or_send_status_message(
                    chat_id=chat_id,
                    message_id=message_ids_by_file_id[file_id],
                    text=text,
                    reply_markup=reply_markup,
                    context=context,
                )
                message_ids_by_file_id[file_id] = message_id
                last_status_by_file_id[file_id] = status

            if not pending:
                break

            await asyncio.sleep(IMPORT_STATUS_POLL_SECONDS)
    finally:
        _batch_status_tasks.pop(batch_id, None)


def _start_batch_status_polling(
    *,
    chat_id: int,
    batch_id: str,
    message_ids_by_file_id: Dict[str, int],
    initial_status_by_file_id: Dict[str, str],
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not message_ids_by_file_id:
        return

    if not any(_should_poll_status(status) for status in initial_status_by_file_id.values()):
        return

    existing = _batch_status_tasks.get(batch_id)
    if existing and not existing.done():
        return

    _batch_status_tasks[batch_id] = asyncio.create_task(
        _poll_import_batch_statuses(
            chat_id=chat_id,
            batch_id=batch_id,
            message_ids_by_file_id=message_ids_by_file_id,
            initial_status_by_file_id=initial_status_by_file_id,
            context=context,
        )
    )


async def _queue_uploads_and_report(
    *,
    chat_id: int,
    uploads: List[PendingUpload],
    context: ContextTypes.DEFAULT_TYPE,
    source: str = "telegram",
) -> None:
    try:
        started_at = time.monotonic()
        payload = await _post_import_files(source=source, uploads=uploads)
        logger.info(
            "Queued %d file(s) in %.2fs.",
            len(uploads),
            time.monotonic() - started_at,
        )
        message_ids_by_file_id = await _send_per_file_status_messages(
            chat_id=chat_id,
            uploads=uploads,
            batch_payload=payload,
            context=context,
        )

        batch_id = payload.get("id") or ""
        files_info = payload.get("files") or []
        initial_status_by_file_id = {
            (item or {}).get("id") or "": (item or {}).get("status") or "unknown"
            for item in files_info
            if (item or {}).get("id")
        }
        if batch_id:
            _start_batch_status_polling(
                chat_id=chat_id,
                batch_id=batch_id,
                message_ids_by_file_id=message_ids_by_file_id,
                initial_status_by_file_id=initial_status_by_file_id,
                context=context,
            )
    except requests.HTTPError as exc:
        logger.warning("Import request returned HTTP error: %s", exc, exc_info=True)
        # API returned an error response; notify once per file to match user expectations.
        detail = str(exc)
        for upload in uploads:
            retry_token = _cache_retry_upload(
                source=source,
                upload=PendingUpload(file_name=upload.file_name, file_bytes=upload.file_bytes),
            )
            reply_markup = _build_status_keyboard(include_retry=True, retry_token=retry_token)
            text = "\n".join(
                [
                    f"File: {upload.file_name}",
                    "Status: failed",
                    "Actions:",
                    f"- Import request failed: {detail}",
                ]
            )
            if upload.status_message_id is not None:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=upload.status_message_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                    continue
                except Exception:
                    pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=upload.message_id,
                reply_markup=reply_markup,
            )
    except Exception as exc:  # pragma: no cover - safety fallback
        logger.warning("Import request failed unexpectedly: %s", exc, exc_info=True)
        for upload in uploads:
            retry_token = _cache_retry_upload(
                source=source,
                upload=PendingUpload(file_name=upload.file_name, file_bytes=upload.file_bytes),
            )
            reply_markup = _build_status_keyboard(include_retry=True, retry_token=retry_token)
            text = "\n".join(
                [
                    f"File: {upload.file_name}",
                    "Status: failed",
                    "Actions:",
                    f"- Import failed: {exc}",
                ]
            )
            if upload.status_message_id is not None:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=upload.status_message_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                    continue
                except Exception:
                    pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=upload.message_id,
                reply_markup=reply_markup,
            )


async def _schedule_media_group_upload(
    *,
    chat_id: int,
    media_group_id: str,
    upload: PendingUpload,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    key = (chat_id, media_group_id)

    async with _media_group_lock:
        _media_group_pending.setdefault(key, []).append(upload)

        existing_task = _media_group_tasks.get(key)
        if existing_task:
            existing_task.cancel()

        async def _delayed() -> None:
            try:
                await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SECONDS)
                async with _media_group_lock:
                    uploads = _media_group_pending.pop(key, [])
                    _media_group_tasks.pop(key, None)
                if uploads:
                    await _queue_uploads_and_report(chat_id=chat_id, uploads=uploads, context=context)
            except asyncio.CancelledError:
                return

        _media_group_tasks[key] = asyncio.create_task(_delayed())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        if update.message:
            await update.message.reply_text("Not authorized.")
        return
    if update.message:
        await update.message.reply_text(
            "Send me a PDF bank statement and I'll queue it for import.\nUse /inbox to review exceptions."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        if update.message:
            await update.message.reply_text("Not authorized.")
        return
    if update.message:
        await update.message.reply_text(
            "\n".join(
                [
                    "Upload PDF statements here. I'll pass them to the import pipeline.",
                    "Use /clean to delete a bad upload or purge all data.",
                    "Use /inbox to review exceptions and transfer suggestions.",
                ]
            )
        )


async def _set_bot_commands(application) -> None:
    try:
        await application.bot.set_my_commands(
            [
                ("start", "Start the bot"),
                ("help", "Show help"),
                ("clean", "Cleanup uploads or purge data"),
                ("inbox", "Review exceptions/transfers"),
            ]
        )
    except Exception as exc:
        logger.warning("Failed to set bot commands: %s", exc, exc_info=True)


async def inbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        if update.message:
            await update.message.reply_text("Not authorized.")
        return
    if not update.message:
        return

    msg = await update.message.reply_text("Loading inbox…")
    await _send_next_queue_item(chat_id=msg.chat_id, context=context, message_id=msg.message_id)


async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        if update.message:
            await update.message.reply_text("Not authorized.")
        return
    if not update.message:
        return

    text = "\n".join(
        [
            "Cleanup menu",
            "Choose what to delete:",
            "- last uploaded file",
            "- a specific file",
            "- ALL data (dangerous)",
        ]
    )
    await update.message.reply_text(text, reply_markup=_build_clean_menu_keyboard())


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.document:
        return

    if not _is_owner(update):
        await message.reply_text("Not authorized.")
        return

    document = message.document
    if document.mime_type != "application/pdf" and not (document.file_name or "").lower().endswith(".pdf"):
        await message.reply_text("Please upload a PDF statement.")
        return

    file = await document.get_file()
    file_bytes = await file.download_as_bytearray()

    chat_id = message.chat_id
    upload = PendingUpload(
        file_name=document.file_name or "statement.pdf",
        file_bytes=bytes(file_bytes),
        message_id=message.message_id,
    )

    # Immediate acknowledgment so the user knows the bot actually received and fetched the file
    # from Telegram, even if the API upload later takes time (e.g., for media groups).
    ack = await message.reply_text(_format_received_ack(upload.file_name, len(upload.file_bytes)))
    upload.status_message_id = ack.message_id

    # Telegram sends multi-file uploads as a media group (album). We debounce to collect
    # the full group, then submit one API request with multiple `files=` parts.
    if message.media_group_id:
        await _schedule_media_group_upload(
            chat_id=chat_id,
            media_group_id=message.media_group_id,
            upload=upload,
            context=context,
        )
        return

    await _queue_uploads_and_report(chat_id=chat_id, uploads=[upload], context=context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        if update.message:
            await update.message.reply_text("Not authorized.")
        return
    if update.message:
        await update.message.reply_text("Upload a PDF statement to begin.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return

    if not _is_owner(update):
        await query.answer("Not authorized.", show_alert=True)
        return

    data = query.data or ""
    if data == "dismiss":
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if data == "queue:next":
        await query.answer()
        await _send_next_queue_item(
            chat_id=query.message.chat_id,
            context=context,
            message_id=query.message.message_id,
        )
        return

    if data.startswith("queue:transfer:confirm:"):
        await query.answer()
        link_id = data.split(":", 3)[3]
        try:
            await _confirm_transfer_link(link_id=link_id)
        except Exception as exc:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"Confirm failed: {exc}")
            return
        await _send_next_queue_item(
            chat_id=query.message.chat_id,
            context=context,
            message_id=query.message.message_id,
        )
        return

    if data.startswith("queue:transfer:reject:"):
        await query.answer()
        link_id = data.split(":", 3)[3]
        try:
            await _reject_transfer_link(link_id=link_id)
        except Exception as exc:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"Reject failed: {exc}")
            return
        await _send_next_queue_item(
            chat_id=query.message.chat_id,
            context=context,
            message_id=query.message.message_id,
        )
        return

    if data.startswith("queue:exc:ac:"):
        await query.answer()
        exception_id = data.split(":", 3)[3]
        try:
            await _approve_exception_category(exception_id=exception_id)
        except Exception as exc:
            await context.bot.send_message(
                chat_id=query.message.chat_id, text=f"Approve category failed: {exc}"
            )
            return
        await _send_next_queue_item(
            chat_id=query.message.chat_id,
            context=context,
            message_id=query.message.message_id,
        )
        return

    if data.startswith("queue:exc:dup:"):
        await query.answer()
        exception_id = data.split(":", 3)[3]
        try:
            await _mark_exception_duplicate(exception_id=exception_id)
        except Exception as exc:
            await context.bot.send_message(
                chat_id=query.message.chat_id, text=f"Mark duplicate failed: {exc}"
            )
            return
        await _send_next_queue_item(
            chat_id=query.message.chat_id,
            context=context,
            message_id=query.message.message_id,
        )
        return

    if data.startswith("queue:exception:resolve:"):
        await query.answer()
        exception_id = data.split(":", 3)[3]
        try:
            await _resolve_exception(exception_id=exception_id)
        except Exception as exc:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"Resolve failed: {exc}")
            return
        await _send_next_queue_item(
            chat_id=query.message.chat_id,
            context=context,
            message_id=query.message.message_id,
        )
        return

    if data == "clean:menu":
        await query.answer()
        try:
            await query.edit_message_text("Cleanup menu", reply_markup=_build_clean_menu_keyboard())
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text="Cleanup menu", reply_markup=_build_clean_menu_keyboard())
        return

    if data == "clean:delete_last":
        await query.answer()
        try:
            await query.edit_message_text("Loading recent uploads…", reply_markup=_build_clean_menu_keyboard())
        except Exception:
            pass

        try:
            files = await _get_import_files(limit=1, offset=0)
        except Exception as exc:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"Failed to load recent uploads: {exc}",
                reply_markup=_build_clean_menu_keyboard(),
            )
            return

        if not files:
            try:
                await query.edit_message_text("No uploads found.", reply_markup=_build_clean_menu_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat_id, text="No uploads found.", reply_markup=_build_clean_menu_keyboard())
            return

        item = files[0]
        file_id = (item or {}).get("id") or ""
        file_name = (item or {}).get("file_name") or "unknown"
        status = (item or {}).get("status") or "unknown"
        file_hash = (item or {}).get("file_hash") or ""
        created_at = (item or {}).get("created_at") or ""

        details = "\n".join(
            [
                "Delete last uploaded file?",
                f"- File: {file_name}",
                f"- Status: {status}",
                f"- Created: {created_at}",
                f"- Hash: {file_hash}",
                f"- Id: {file_id}",
            ]
        )
        try:
            await query.edit_message_text(details, reply_markup=_build_clean_confirm_delete_keyboard(file_id))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=details,
                reply_markup=_build_clean_confirm_delete_keyboard(file_id),
            )
        return

    if data.startswith("clean:list:"):
        await query.answer()
        try:
            offset = int(data.split(":", 2)[2])
        except Exception:
            offset = 0
        limit = 8

        try:
            files = await _get_import_files(limit=limit, offset=offset)
        except Exception as exc:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"Failed to load uploads: {exc}",
                reply_markup=_build_clean_menu_keyboard(),
            )
            return

        if not files:
            try:
                await query.edit_message_text("No uploads found.", reply_markup=_build_clean_menu_keyboard())
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat_id, text="No uploads found.", reply_markup=_build_clean_menu_keyboard())
            return

        lines = ["Select a file to delete:"]
        for idx, item in enumerate(files, start=1):
            file_name = (item or {}).get("file_name") or "unknown"
            status = (item or {}).get("status") or "unknown"
            created_at = (item or {}).get("created_at") or ""
            file_hash = (item or {}).get("file_hash") or ""
            file_id = (item or {}).get("id") or ""
            lines.append(f"{idx}. {file_name} [{status}] {created_at} {file_hash} ({file_id})")

        text = "\n".join(lines)
        try:
            await query.edit_message_text(
                text,
                reply_markup=_build_clean_list_keyboard(files, offset=offset, limit=limit),
            )
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=_build_clean_list_keyboard(files, offset=offset, limit=limit),
            )
        return

    if data.startswith("clean:ask_delete:"):
        await query.answer()
        file_id = data.split(":", 2)[2]

        try:
            files = await _get_import_files(limit=50, offset=0)
        except Exception:
            files = []
        item = next((f for f in files if (f or {}).get("id") == file_id), None) or {}

        file_name = (item or {}).get("file_name") or "unknown"
        status = (item or {}).get("status") or "unknown"
        file_hash = (item or {}).get("file_hash") or ""
        created_at = (item or {}).get("created_at") or ""

        details = "\n".join(
            [
                "Delete this file?",
                f"- File: {file_name}",
                f"- Status: {status}",
                f"- Created: {created_at}",
                f"- Hash: {file_hash}",
                f"- Id: {file_id}",
            ]
        )
        try:
            await query.edit_message_text(details, reply_markup=_build_clean_confirm_delete_keyboard(file_id))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=details,
                reply_markup=_build_clean_confirm_delete_keyboard(file_id),
            )
        return

    if data.startswith("clean:confirm_delete:"):
        await query.answer()
        file_id = data.split(":", 2)[2]
        try:
            await query.edit_message_text("Deleting…", reply_markup=_build_clean_menu_keyboard())
        except Exception:
            pass

        try:
            result = await _delete_import_file(file_id=file_id)
        except Exception as exc:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"Delete failed: {exc}",
                reply_markup=_build_clean_menu_keyboard(),
            )
            return

        text = "\n".join(
            [
                "Delete complete",
                f"- file_id: {result.get('file_id')}",
                f"- batch_id: {result.get('batch_id')}",
                f"- deleted_statements: {result.get('deleted_statements')}",
                f"- deleted_statement_rows: {result.get('deleted_statement_rows')}",
                f"- deleted_transactions: {result.get('deleted_transactions')}",
                f"- deleted_balance_snapshots: {result.get('deleted_balance_snapshots')}",
                f"- deleted_exceptions: {result.get('deleted_exceptions')}",
                f"- deleted_import_batch: {result.get('deleted_import_batch')}",
                f"- deleted_disk_file: {result.get('deleted_disk_file')}",
            ]
        )
        try:
            await query.edit_message_text(text, reply_markup=_build_clean_menu_keyboard())
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=_build_clean_menu_keyboard())
        return

    if data == "clean:purge_warn":
        await query.answer()
        warning = "\n".join(
            [
                "Delete ALL data?",
                "This will remove:",
                "- imports, statements, statement rows, transactions",
                "- exceptions, balance snapshots",
                "- rules and accounts",
                "- uploaded PDF files on disk",
                "",
                "This cannot be undone.",
            ]
        )
        try:
            await query.edit_message_text(warning, reply_markup=_build_clean_purge_warn_keyboard())
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=warning, reply_markup=_build_clean_purge_warn_keyboard())
        return

    if data == "clean:purge_confirm":
        await query.answer()
        warning = "\n".join(
            [
                "FINAL CONFIRMATION",
                "Press DELETE ALL to permanently remove all data.",
            ]
        )
        try:
            await query.edit_message_text(warning, reply_markup=_build_clean_purge_confirm_keyboard())
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=warning, reply_markup=_build_clean_purge_confirm_keyboard())
        return

    if data == "clean:purge_execute":
        await query.answer()
        try:
            await query.edit_message_text("Purging all data…", reply_markup=_build_clean_menu_keyboard())
        except Exception:
            pass

        try:
            result = await _purge_data()
        except Exception as exc:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"Purge failed: {exc}",
                reply_markup=_build_clean_menu_keyboard(),
            )
            return

        text = "\n".join(
            [
                "Purge complete",
                f"- deleted_import_batches: {result.get('deleted_import_batches')}",
                f"- deleted_import_files: {result.get('deleted_import_files')}",
                f"- deleted_statements: {result.get('deleted_statements')}",
                f"- deleted_statement_rows: {result.get('deleted_statement_rows')}",
                f"- deleted_transactions: {result.get('deleted_transactions')}",
                f"- deleted_transfer_links: {result.get('deleted_transfer_links')}",
                f"- deleted_balance_snapshots: {result.get('deleted_balance_snapshots')}",
                f"- deleted_exceptions: {result.get('deleted_exceptions')}",
                f"- deleted_rules: {result.get('deleted_rules')}",
                f"- deleted_accounts: {result.get('deleted_accounts')}",
                f"- deleted_disk_files: {result.get('deleted_disk_files')}",
            ]
        )
        try:
            await query.edit_message_text(text, reply_markup=_build_clean_menu_keyboard())
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=_build_clean_menu_keyboard())
        return

    if not data.startswith("retry:"):
        await query.answer()
        return

    token = data.split(":", 1)[1]
    _prune_retry_cache()
    item = _retry_cache.get(token)
    if not item:
        await query.answer("Retry expired. Please re-upload the PDF.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=_build_status_keyboard(include_retry=False))
        except Exception:
            pass
        return

    await query.answer()
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    retrying_text = "\n".join(
        [
            f"File: {item.upload.file_name}",
            "Status: retrying",
            "Actions:",
            "- Re-uploading to import queue...",
        ]
    )
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=retrying_text,
            reply_markup=_build_status_keyboard(include_retry=False),
        )
    except Exception:
        pass

    try:
        payload = await _post_import_files(source=item.source, uploads=[item.upload])
        batch_id = payload.get("id") or ""
        files_info = payload.get("files") or []
        file_info = files_info[0] if files_info else {}
        text = _format_file_result(batch_id=batch_id, file_info=file_info)
        reply_markup = _build_status_keyboard(include_retry=False)
        updated_message_id = await _edit_or_send_status_message(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            context=context,
        )

        file_id = (file_info or {}).get("id") or ""
        status = (file_info or {}).get("status") or "unknown"
        if batch_id and file_id:
            _start_batch_status_polling(
                chat_id=chat_id,
                batch_id=batch_id,
                message_ids_by_file_id={file_id: updated_message_id},
                initial_status_by_file_id={file_id: status},
                context=context,
            )
    except Exception as exc:
        failed_text = "\n".join(
            [
                f"File: {item.upload.file_name}",
                "Status: failed",
                "Actions:",
                f"- Retry failed: {exc}",
            ]
        )
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=failed_text,
            reply_markup=_build_status_keyboard(include_retry=True, retry_token=token),
        )


def main() -> None:
    _configure_logging()

    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set")
    # Avoid leaking the token in tracebacks emitted by the Telegram SDK on invalid tokens.
    ok = False
    for attempt in range(1, API_RETRY_MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getMe",
                timeout=20,
                allow_redirects=False,
            )
            if resp.status_code == 200 and bool(resp.json().get("ok")):
                ok = True
                break
            if resp.status_code in {429, 500, 502, 503, 504} and attempt < API_RETRY_MAX_ATTEMPTS:
                delay = _compute_backoff_seconds(attempt + 1)
                logger.warning(
                    "Telegram getMe returned status %s (attempt %d/%d). Retrying in %.2fs.",
                    resp.status_code,
                    attempt,
                    API_RETRY_MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
                continue
            break
        except Exception as exc:
            if attempt < API_RETRY_MAX_ATTEMPTS:
                delay = _compute_backoff_seconds(attempt + 1)
                logger.warning(
                    "Telegram getMe failed (attempt %d/%d): %s. Retrying in %.2fs.",
                    attempt,
                    API_RETRY_MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)
                continue
            break
    if not ok:
        raise SystemExit("BOT_TOKEN is invalid (Telegram getMe failed)")

    logger.info("Starting bot with API base URL %s", API_BASE_URL)
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(_set_bot_commands).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clean", clean_command))
    app.add_handler(CommandHandler("inbox", inbox_command))
    app.add_handler(CommandHandler("exceptions", inbox_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()


if __name__ == "__main__":
    main()
