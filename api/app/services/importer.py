from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_FILENAME_LEN = 160


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize_filename(filename: str | None) -> str:
    raw_name = Path((filename or "").strip()).name
    if not raw_name:
        raw_name = "statement.pdf"

    sanitized = _SAFE_FILENAME_RE.sub("_", raw_name).strip("._")
    if not sanitized:
        sanitized = "statement.pdf"

    if len(sanitized) > _MAX_FILENAME_LEN:
        stem, suffix = os.path.splitext(sanitized)
        allowed_stem_len = max(1, _MAX_FILENAME_LEN - len(suffix))
        sanitized = f"{stem[:allowed_stem_len]}{suffix}"

    return sanitized


def save_upload(upload: UploadFile, dest_dir: str) -> tuple[str, str]:
    ensure_dir(dest_dir)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    unique_suffix = uuid.uuid4().hex[:8]
    safe_name = _sanitize_filename(upload.filename)
    file_name = f"{timestamp}_{unique_suffix}_{safe_name}"
    file_path = os.path.join(dest_dir, file_name)

    try:
        upload.file.seek(0)
    except Exception:
        pass

    with open(file_path, "wb") as out:
        shutil.copyfileobj(upload.file, out)
    file_hash = hash_file(file_path)
    return file_path, file_hash


def is_pdf(upload: UploadFile) -> bool:
    if upload.filename and upload.filename.lower().endswith(".pdf"):
        return True
    content_type = (upload.content_type or "").lower()
    return content_type == "application/pdf"
