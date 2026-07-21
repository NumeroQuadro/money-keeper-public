from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_cors_options(origins_csv: str) -> tuple[list[str], bool]:
    origins = [origin.strip() for origin in (origins_csv or "").split(",") if origin.strip()]
    if not origins:
        origins = ["*"]

    wildcard = "*" in origins
    if wildcard and len(origins) > 1:
        logger.warning(
            "CORS_ALLOWED_ORIGINS contains '*' plus explicit origins; wildcard takes precedence."
        )
        origins = ["*"]

    allow_credentials = not wildcard
    if wildcard:
        logger.warning(
            "CORS wildcard origin detected; disabling allow_credentials to avoid insecure or invalid CORS behavior."
        )
    return origins, allow_credentials
