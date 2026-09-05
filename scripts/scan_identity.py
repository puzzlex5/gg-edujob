#!/usr/bin/env python3
"""Shared immutable reconciliation scan identity.

The workflow mints RECONCILE_SCAN_ID exactly once per run. Scripts may read or
require it, but must never fabricate a fallback identity on their own.
"""
from __future__ import annotations

import os
import re

ENV_NAME = "RECONCILE_SCAN_ID"
SCAN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def current_scan_id() -> str | None:
    raw = str(os.environ.get(ENV_NAME) or "").strip()
    if not raw:
        return None
    if not SCAN_ID_RE.fullmatch(raw):
        raise RuntimeError(f"Invalid {ENV_NAME}={raw!r}")
    return raw


def require_scan_id() -> str:
    scan_id = current_scan_id()
    if not scan_id:
        raise RuntimeError(f"{ENV_NAME} is required for reconciliation proof generation")
    return scan_id
