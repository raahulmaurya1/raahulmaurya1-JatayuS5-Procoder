"""
app/agents/fast_path_router.py
================================
Layer 1 — Deterministic Fast Path Router

Intercepts machine-generated or highly predictable inputs BEFORE they
reach the Gemini LLM Slow Path.  Every match here saves ~1–3 seconds
of round-trip latency and one API call.

Design Rules
------------
- Pure function: no database access, no Redis access, no side effects.
- Returns ``{"route": str, "extracted": dict}`` on a hit, or ``None``
  to let the caller fall through to the next layer.
- The ULID regex is guarded: it only fires if the session_state
  indicates a lifecycle flow is awaiting an Account ID.
- Generic phrases like "open a bank account" are NEVER matched here.
  They MUST fall through to the LLM so the orchestrator can ask the
  user what type of account they want.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# COMPILED PATTERNS (module-level for performance)
# ---------------------------------------------------------------------------

# OTP codes: "Phone OTP: 123456" or "Email OTP: 789012"
_PHONE_OTP_RE = re.compile(r"^Phone\s+OTP:\s*(\d{4,8})$", re.IGNORECASE)
_EMAIL_OTP_RE = re.compile(r"^Email\s+OTP:\s*(\d{4,8})$", re.IGNORECASE)

# ULID: Crockford Base32, exactly 26 characters
# https://github.com/ulid/spec
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

# Exact system triggers — sent by the frontend, never ambiguous
_EXACT_ROUTES: Dict[str, Dict[str, Any]] = {
    "SYSTEM: TRIGGER_OTP_SEND": {
        "route": "trigger_otp_send",
        "extracted": {},
    },
    "SYSTEM: TRIGGER_EMAIL_OTP": {
        "route": "trigger_email_otp_send",
        "extracted": {},
    },
    "SYSTEM: FACE_VERIFICATION_SUCCESSFUL": {
        "route": "face_verification_success",
        "extracted": {},
    },
    "SYSTEM: SUBMIT_ADDITIONAL_INFO": {
        "route": "submit_additional_info",
        "extracted": {},
    },
}


def try_fast_path(
    user_message: str,
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Attempt to route *user_message* deterministically.

    Parameters
    ----------
    user_message:
        The raw message string from the frontend / orchestrator.
    session_state:
        Optional dict containing keys like ``lifecycle_awaiting_id``
        (bool or truthy), ``session_ulid``, etc.  Used exclusively
        for the ULID guard.

    Returns
    -------
    dict or None
        ``{"route": str, "extracted": dict}`` on a hit.
        ``None`` if no deterministic match — caller should try the
        next layer (semantic cache → LLM).
    """
    if not user_message:
        return None

    stripped = user_message.strip()

    # ── 1. Exact system triggers ──────────────────────────────────────────
    exact_hit = _EXACT_ROUTES.get(stripped)
    if exact_hit:
        logger.info(f"[FAST_PATH] ⚡ Exact match: '{stripped}' → {exact_hit['route']}")
        return exact_hit

    # ── 2. OTP regex ──────────────────────────────────────────────────────
    phone_match = _PHONE_OTP_RE.match(stripped)
    if phone_match:
        code = phone_match.group(1)
        logger.info(f"[FAST_PATH] ⚡ Phone OTP detected: {code}")
        return {
            "route": "phone_otp_submit",
            "extracted": {"otp_code": code, "otp_type": "phone"},
        }

    email_match = _EMAIL_OTP_RE.match(stripped)
    if email_match:
        code = email_match.group(1)
        logger.info(f"[FAST_PATH] ⚡ Email OTP detected: {code}")
        return {
            "route": "email_otp_submit",
            "extracted": {"otp_code": code, "otp_type": "email"},
        }

    # ── 3. ULID regex (guarded — lifecycle flow only) ─────────────────────
    if _ULID_RE.match(stripped):
        state = session_state or {}
        if state.get("lifecycle_awaiting_id"):
            logger.info(f"[FAST_PATH] ⚡ ULID Account ID detected: {stripped}")
            return {
                "route": "lifecycle_account_id",
                "extracted": {"account_id": stripped},
            }
        # If no lifecycle flag, this might be a session_ulid or other
        # data — do NOT intercept.  Let it fall through.
        logger.debug(f"[FAST_PATH] ULID-like input '{stripped}' ignored (no lifecycle flag)")

    return None
