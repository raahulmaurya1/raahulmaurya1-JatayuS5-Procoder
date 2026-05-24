import asyncio
import json
import re
from loguru import logger
import google.generativeai as genai
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models.user import UserInitial
from app.db.models.document import UserDocument
from app.storage.redis import redis_client
from app.services.otp_service import send_phone_otp, send_email_otp, verify_phone_otp, verify_email_otp
from app.agents.intent_agent import classify_intent
from app.agents.memory_agent import query_similar_cases
from app.agents.risk_agent import evaluate_full_risk
from ulid import ULID

# ---------------------------------------------------------------------------
# GEO HELPERS
# ---------------------------------------------------------------------------

import ipaddress

# Maps E.164 country calling codes to ISO-3166-1 alpha-2.
# Extend as needed; longest-prefix match is used so +1 (US/CA) must come
# BEFORE any overlapping shorter prefix.
_PHONE_PREFIX_MAP: list[tuple[str, str]] = [
    ("+91",  "IN"),  # India
    ("+1",   "US"),  # USA / Canada
    ("+44",  "GB"),  # United Kingdom
    ("+61",  "AU"),  # Australia
    ("+971", "AE"),  # UAE
    ("+65",  "SG"),  # Singapore
    ("+49",  "DE"),  # Germany
    ("+33",  "FR"),  # France
    ("+81",  "JP"),  # Japan
    ("+86",  "CN"),  # China
    ("+7",   "RU"),  # Russia
]


def _phone_prefix_to_country(phone: str) -> str:
    """
    Derive an ISO-3166-1 alpha-2 country code from a phone number's
    E.164 calling-code prefix.

    Falls back to ``"IN"`` (the platform's home market) for unknown prefixes
    or empty / non-E.164 strings, so the comparison is still valid for
    domestic numbers stored without a leading ``+``.
    """
    if not phone:
        return "IN"
    # Normalise: ensure there's a leading '+'
    normalised = phone.strip()
    if not normalised.startswith("+"):
        normalised = "+" + normalised
    # Longest-prefix match
    for prefix, country in sorted(_PHONE_PREFIX_MAP, key=lambda x: -len(x[0])):
        if normalised.startswith(prefix):
            return country
    return "IN"  # default: India


_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),   # CGN
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("fc00::/7"),         # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]


def _ip_to_country(ip: str) -> Optional[str]:
    """
    Resolve an IP address string to an ISO-3166-1 alpha-2 country code.

    Returns ``None`` for:
    - Loopback addresses (``127.0.0.1``, ``::1``)
    - RFC-1918 / private / link-local ranges
    - Unparseable strings

    When ``None`` is returned the caller should **omit**
    ``ip_geolocation_country`` from ``telemetry_data`` entirely, so
    ``_run_tier2`` skips the geo check (both fields must be present and
    non-empty for the check to fire).

    For now this uses a stub that returns ``"IN"`` for all routable public
    IPs.  Replace the stub block with a real MaxMind / ip-api call when
    a GeoIP database is available.
    """
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        logger.debug(f"[GeoHelper] Could not parse IP: {ip!r}")
        return None

    # Loopback
    if addr.is_loopback:
        logger.debug(f"[GeoHelper] Skipping loopback IP: {ip}")
        return None

    # Private / reserved ranges
    for net in _PRIVATE_NETS:
        try:
            if addr in net:
                logger.debug(f"[GeoHelper] Skipping private/reserved IP: {ip}")
                return None
        except TypeError:
            pass  # IPv4 addr vs IPv6 net — skip silently

    # ── STUB: replace with real GeoIP lookup ─────────────────────────────
    # e.g.: reader = geoip2.database.Reader('GeoLite2-Country.mmdb')
    #        return reader.country(ip).country.iso_code
    return "IN"  # default stub: assume India for all public IPs


# ---------------------------------------------------------------------------
# RISK ROUTING HELPER
# ---------------------------------------------------------------------------

async def _apply_risk_routing(
    user: UserInitial,
    db: AsyncSession,
    session_ulid: str,
    final_data: Optional[Dict[str, Any]] = None,
    face_result: Optional[Dict[str, Any]] = None,
    current_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluate the 3-tier risk score and return the appropriate UI routing dict.

    Called AFTER all verification steps are complete, BEFORE a success response
    is returned to the frontend.  Implements the OCP hook — the risk pipeline
    is never modified; only wired into the orchestrator here.

    Returns one of three routing dicts:
        PROCEED   -> caller's original success action (pass-through)
        RETRY     -> RENDER_FACE_VERIFICATION  (minor biometric anomaly)
        ESCALATE  -> RENDER_HUMAN_REVIEW + DB status MANUAL_REVIEW (high risk)

    Errors are logged and default to PROCEED so the onboarding flow is never
    blocked by a risk-engine failure (fail-open per spec).
    """
    try:
        _cs = current_state or {}

        # ── Build user_record (no raw PII — dob/email used only for scoring)
        _vdata = final_data or getattr(user, "verified_data", {}) or {}
        
        _raw_phone: str = getattr(user, "phone", "") or ""
        user_record: Dict[str, Any] = {
            "dob":           getattr(user, "dob", None),
            "email":         getattr(user, "email", None),
            # Derive country from the stored E.164 phone prefix (e.g. +91 → IN)
            "phone_country": _phone_prefix_to_country(_raw_phone),
            "aadhaar_name":  _vdata.get("aadhaar_name") or getattr(user, "name", None),
            "pan_name":      _vdata.get("pan_name") or getattr(user, "name", None),
            "account_type":  getattr(user, "account_type", "retail_savings"),
        }

        # ── Build telemetry_data from face result + request telemetry ────────
        telemetry_data: Dict[str, Any] = {
            "request_id": session_ulid,
        }

        # ── FIELD 1: IP & Time (injected by decision_routes.py) ──────────────
        # Resolve the raw client IP to an ISO country code.  Returns None for
        # loopback / private addresses — in that case we intentionally omit the
        # key so _run_tier2 skips the geo check entirely (avoids false positives
        # in local / NAT environments).
        _raw_client_ip: str = _cs.get("client_ip", "")
        _resolved_country = _ip_to_country(_raw_client_ip)
        if _resolved_country:
            telemetry_data["ip_geolocation_country"] = _resolved_country
            logger.debug(f"[RiskHook] IP {_raw_client_ip!r} resolved to country: {_resolved_country}")
        else:
            logger.debug(f"[RiskHook] IP {_raw_client_ip!r} is loopback/private — geo check skipped")
        if _cs.get("account_created_at_utc"):
            telemetry_data["account_created_at_utc"] = _cs["account_created_at_utc"]

        # ── FIELD 2: Face & Blink (nested structure from face_verification_tasks.py)
        if face_result and isinstance(face_result, dict):
            # face_verification_tasks.py stores data under nested keys:
            #   face_result["face_verification"]["average_similarity"]
            #   face_result["liveness"]["blink_count"]
            #   face_result["liveness"]["confidence"]
            _fv = face_result.get("face_verification", {})
            _lv = face_result.get("liveness", {})
            
            # Explicit None checks to avoid '0' being treated as missing (Requirement 2)
            sim = _fv.get("average_similarity")
            if sim is None:
                sim = face_result.get("face_similarity")
            if sim is None:
                sim = face_result.get("similarity_score")
            telemetry_data["face_similarity"] = sim

            blinks = _lv.get("blink_count")
            if blinks is None:
                blinks = face_result.get("blink_count")
            if blinks is None:
                blinks = face_result.get("blinks_detected")
            telemetry_data["blink_count"] = blinks

            conf = _lv.get("confidence")
            if conf is None:
                conf = face_result.get("liveness_confidence")
            if conf is None:
                conf = face_result.get("confidence")
            telemetry_data["liveness_confidence"] = conf

        # ── FIELD 3: OTP Retries (from Redis rate-limit / verify-attempts keys)
        try:
            _phone = getattr(user, "phone", None)
            _otp_retries = 0
            if _phone:
                # rate_limit:{phone} tracks total OTP send attempts (3-min window)
                _rl_raw = await redis_client.get(f"rate_limit:{_phone}")
                if _rl_raw:
                    _otp_retries += max(0, int(_rl_raw) - 1)  # first attempt is not a "retry"
                # verify_attempts:{phone} tracks failed verification attempts
                # (deleted on success, so may be absent)
                _va_raw = await redis_client.get(f"verify_attempts:{_phone}")
                if _va_raw:
                    _otp_retries += int(_va_raw)
            telemetry_data["otp_retries"] = _otp_retries
        except Exception as otp_exc:
            logger.debug(f"[RiskHook] OTP retry lookup failed (non-fatal): {otp_exc}")
            telemetry_data["otp_retries"] = 0

        # ── FIELD 4: SME Business Data (industry & turnover) ─────────────────
        additional_info_record: Dict[str, Any] = {}
        if final_data and isinstance(final_data, dict):
            additional_info_record = final_data
            # The UI form schema uses dot-notation keys like:
            #   "business_profile.industry_nic_code"
            #   "business_profile.expected_annual_turnover"
            # Handle both nested dict and flat dot-notation keys from the form
            bp = final_data.get("business_profile", {})
            if isinstance(bp, dict) and bp:
                _nic = bp.get("industry_nic_code") or bp.get("industry_nic")
                _trn = bp.get("expected_annual_turnover") or bp.get("expected_turnover")
                if _nic:
                    additional_info_record.setdefault("industry_nic", _nic)
                if _trn:
                    additional_info_record.setdefault("expected_turnover", _trn)
                additional_info_record.setdefault("occupation_type",    bp.get("occupation_type"))
                additional_info_record.setdefault("annual_income",      bp.get("annual_income"))
                additional_info_record.setdefault("pep_status",         bp.get("pep_status", False))

            # Flat dot-notation keys override (some form submissions use these)
            if "business_profile.industry_nic_code" in final_data:
                additional_info_record["industry_nic"] = final_data["business_profile.industry_nic_code"]
            if "business_profile.expected_annual_turnover" in final_data:
                additional_info_record["expected_turnover"] = final_data["business_profile.expected_annual_turnover"]

        _tkeys = [k for k in telemetry_data if k != "request_id"]
        logger.info(
            f"[RiskHook] Invoking evaluate_full_risk for session={session_ulid} account_type={user_record.get('account_type')} | telemetry_keys={_tkeys}"
        )
        logger.info(
            f"[RiskHook] Biometric telemetry: face_similarity={telemetry_data.get('face_similarity')} "
            f"blink_count={telemetry_data.get('blink_count')} liveness_confidence={telemetry_data.get('liveness_confidence')} "
            f"| face_result_present={bool(face_result)}"
        )

        # ── Phase 4 Hook: evaluate full risk (store is fire-and-forget inside)
        risk_result = await evaluate_full_risk(
            user_record=user_record,
            telemetry_data=telemetry_data,
            additional_info_record=additional_info_record,
        )

        category    = risk_result.get("category", "AUTO_APPROVE")
        total_score = risk_result.get("score", 0)
        risk_flags  = risk_result.get("flags", [])

        logger.info(f"[OnboardAI][RISK] Final Decision | Category: {category} | Score: {total_score} | Flags: {risk_flags}")

        # ── Phase 5: Decision Routing ─────────────────────────────────────────
        async def update_session_status(sid: str, new_status: str):
            stmt = (
                update(UserInitial)
                .where(UserInitial.id == sid)
                .values(status=new_status)
            )
            await db.execute(stmt)
            await db.commit()

        if category == "REJECT":
            # Hard stop. Do not override.
            await update_session_status(session_ulid, "REJECTED")
            return {
                "_risk_action": "REJECT",
                "ui_action": "RENDER_REJECTED",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": "Your application could not be approved.",
                # Exact requested keys structure:
                "action": "RENDER_REJECTED",
                "message": "Your application could not be approved."
            }

        elif category == "MANUAL_REVIEW":
            # Escalate to human
            await update_session_status(session_ulid, "UNDER_REVIEW")

            # ── Fire review escalation email (fire-and-forget) ────────────────
            try:
                from app.services.email_service import send_review_email
                asyncio.create_task(send_review_email(
                    email=getattr(user, "email", None) or "",
                    name=getattr(user, "name", None) or "",
                    application_id=session_ulid,
                ))
                logger.info(f"[OnboardAI][EMAIL] Review escalation email task queued for {session_ulid}")
            except Exception as _rev_email_err:
                logger.warning(f"[OnboardAI][EMAIL] Non-fatal: review email task failed: {_rev_email_err}")

            return {
                "_risk_action": "ESCALATE",
                "ui_action": "RENDER_HUMAN_REVIEW",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": "Your application is under manual review.",
                # Exact requested keys structure:
                "action": "RENDER_REVIEW",
                "message": "Your application is under manual review."
            }

        elif category == "AUTO_REJECT":
            # Hard stop due to risk engine.
            await update_session_status(session_ulid, "REJECTED")

            # ── Fire auto rejection email (fire-and-forget) ────────────────
            try:
                from app.services.email_service import send_auto_rejection_email
                asyncio.create_task(send_auto_rejection_email(
                    email=getattr(user, "email", None) or "",
                    name=getattr(user, "name", None) or "",
                    application_id=session_ulid,
                ))
                logger.info(f"[OnboardAI][EMAIL] Auto rejection email task queued for {session_ulid}")
            except Exception as _rev_email_err:
                logger.warning(f"[OnboardAI][EMAIL] Non-fatal: auto rejection email task failed: {_rev_email_err}")

            return {
                "_risk_action": "AUTO_REJECT",
                "ui_action": "RENDER_AUTO_REJECT",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": "Your application has been auto rejected.",
                "action": "RENDER_AUTO_REJECT",
                "message": "Your application has been auto rejected."
            }

        elif category == "AUTO_APPROVE" or total_score == 0:
            # Proceed to next onboarding step (e.g., account creation)
            await update_session_status(session_ulid, "APPROVED")
            return {
                "_risk_action": "PROCEED",
                # Exact requested keys structure:
                "action": "PROCEED",
                "next_step": "RENDER_AUTO_APPROVE",
                "ui_action": "RENDER_AUTO_APPROVE",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": "All steps are complete! Your application is being processed."
            }
        
        return {"_risk_action": "PROCEED"}

    except Exception as exc:
        # Fail-open: log the error, never block the onboarding flow
        logger.exception(
            "[RiskHook] evaluate_full_risk failed for session={session_ulid} — defaulting to PROCEED: {exc}",
            session_ulid=session_ulid, exc=exc
        )
        return {"_risk_action": "PROCEED"}


# --- 1. TOOL DECLARATIONS ---
# We define clean, synchronous-looking schemas so Gemini knows exactly what it can trigger natively.

def trigger_phone_otp(phone: str) -> dict:
    """Sends a strict 6-digit SMS OTP to the provided phone number to begin the onboarding process."""
    pass

def trigger_email_otp(email: str, pending_session_id: str) -> dict:
    """Sends a verification email. Only use this if the phone has already been verified and you have the pending_session_id."""
    pass

def submit_email_otp(code: str) -> dict:
    """Verifies the 6-digit Email OTP submitted by the user and finalizes authentication."""
    pass

def submit_phone_otp(phone: str, code: str) -> dict:
    """Verifies the 6-digit SMS OTP submitted by the user and generates the initial session_ulid."""
    pass

def classify_user_intent(user_message: str) -> dict:
    """If the user asks to open an account or states their intent, this classifies them into Retail, SME, etc."""
    pass

def request_document_upload() -> dict:
    """Triggers the React Frontend to display the PAN/Aadhaar document upload screen."""
    pass

def extract_and_review_tool(session_ulid: str) -> dict:
    """Fetches OCR extraction result after user uploads documents and triggers Data Review UI."""
    pass

def trigger_face_verification_tool(session_ulid: str) -> dict:
    """Triggers the React Frontend to display the Face Verification / Liveness capture screen."""
    pass

def execute_hybrid_freeze_tool(session_ulid: str) -> dict:
    """Runs data de-duplication and saves the confirmed user profile, calculating a Risk Score."""
    pass
    
def escalate_to_human_tool(reason: str) -> dict:
    """Escalates the session to a human Ops Maker/Checker review queue if risk is too high."""
    pass
    
def search_similar_cases(context: str) -> dict:
    """Queries the centralized pgvector memory database for historical edge cases matching the given user scenario context to align current risk decisions."""
    pass

# Map schemas
tool_registry_schemas = [
    trigger_phone_otp, 
    trigger_email_otp, 
    submit_phone_otp,
    submit_email_otp,
    classify_user_intent, 
    request_document_upload,
    extract_and_review_tool,
    trigger_face_verification_tool,
    execute_hybrid_freeze_tool,
    escalate_to_human_tool,
    search_similar_cases
]

# --- 2. ORCHESTRATION ENGINE ---

async def get_dynamic_state(session_ulid: Optional[str], db: AsyncSession) -> dict:
    """Resolves the active memory profile from Postgres and Redis to feed the Agent context."""
    state = {
        "isAuthenticated": False,
        "phoneVerified": False,
        "emailVerified": False,
        "kycUploaded": False,
        "finalized": False,
        "profile": {}
    }
    
    if not session_ulid:
        return state
        
    # Check PostgreSQL for finalized or verified states
    result = await db.execute(
        select(UserInitial)
        .where(UserInitial.id == session_ulid)
        .options(selectinload(UserInitial.additional_info))
    )
    user = result.scalar_one_or_none()
    
    if user:
        state["isAuthenticated"] = True
        state["phoneVerified"] = bool(user.phone)
        state["emailVerified"] = bool(user.email)
        state["finalized"] = user.status == "FINALIZED"
        state["intent"] = user.account_type
        
        # ── Lifecycle Logic: Prioritize current session verification ──
        # If there's a pending auth in Redis for this UID, use IT instead of DB flags.
        pending_raw = await redis_client.get(f"pending_auth:{session_ulid}")
        if pending_raw:
            pending_data = json.loads(pending_raw)
            state["phoneVerified"] = pending_data.get("phone_verified", False)
            state["emailVerified"] = pending_data.get("email_verified", False)
            logger.info(f"[OnboardAI][STATE] Lifecycle check: Using Redis for phone={state['phoneVerified']} email={state['emailVerified']}")

        state["profile"] = {
            "name": user.name,
            "phone": user.phone,
            "email": user.email,
            "status": user.status,
            "accountType": user.account_type
        }
        state["kycUploaded"] = bool(user.raw_archive or user.verified_data)
        state["faceVerified"] = bool(user.face_verified)
        # Deep-inspect additional_info.data for SME accounts:
        _ai_row = user.additional_info
        if _ai_row and isinstance(getattr(_ai_row, "data", None), dict):
            _ai_data = _ai_row.data
            if user.account_type == "sme_current":
                _SME_FORM_KEYS = {"business_profile", "stakeholders"}
                state["additionalInfoCollected"] = bool(_SME_FORM_KEYS & set(_ai_data.keys()))
            else:
                state["additionalInfoCollected"] = bool(_ai_data)
        else:
            state["additionalInfoCollected"] = False
        return state

    # Otherwise, check Redis for volatile pending state mapping
    pending_raw = await redis_client.get(f"pending_auth:{session_ulid}")
    if pending_raw:
        pending_data = json.loads(pending_raw)
        state["phoneVerified"] = pending_data.get("phone_verified", False)
        state["profile"]["phone"] = pending_data.get("phone")
        
    # Check for mapped intent specifically
    intent_raw = await redis_client.get(f"session_intent:{session_ulid}")
    state["intent"] = intent_raw if intent_raw else None
        
    return state


SYSTEM_INSTRUCTION = """
You are the Strict Master Orchestrator for a highly secure Bank Onboarding System.
Your ONLY goal is to guide the user sequentially through the account opening flow:
1. Phone Verification (Collect phone, send OTP, verify OTP -> RENDER_PHONE_AUTH)
2. Email Verification (Collect email, send OTP, verify OTP -> RENDER_EMAIL_AUTH)
3. Intent Classification (Ask what type of account they want -> classify_user_intent -> RENDER_CHAT)
4. KYC Document Upload (Trigger upload UI -> request_document_upload -> RENDER_KYC_UPLOAD)
5. Data Review & Edit (Wait for upload success -> extract_and_review_tool -> RENDER_DATA_REVIEW)
6. Face Verification (Wait for data review confirmation -> trigger_face_verification_tool -> RENDER_FACE_VERIFICATION)
7. Final Approval or Human Escalation (Wait for face verification success -> execute_hybrid_freeze_tool -> RENDER_AUTO_APPROVE or RENDER_HUMAN_REVIEW)

CRITICAL ROUTING RULES:
- You must STRICTLY FOLLOW the 7-step sequence above. Do NOT skip steps.
- CRITICAL: You must NEVER assume the user's account type. If the 'intent' field in the Current User State is null, you MUST return 'ui_action': 'RENDER_CHAT' and explicitly ask the user to select an account type. 
- You are FORBIDDEN from returning 'RENDER_KYC_UPLOAD' until after the user has confirmed their intent and the 'intent' field is populated.
- If KYC documents are reviewed and confirmed (`kycUploaded` is true and data is valid), you MUST transition to Face Verification.
- If the user's message indicates data confirmation or "USER_CONFIRMED_DATA", call `trigger_face_verification_tool` and return `RENDER_FACE_VERIFICATION`.
- [Phase 4 Hook]: If the user's message is "SYSTEM: FACE_VERIFICATION_SUCCESSFUL", you MUST execute `execute_hybrid_freeze_tool`.
- CRITICAL RULE: When the user states their intent (e.g., open a new account or update an existing one), you MUST map their request to one of these exact strings: retail_savings, digital_only, sme_current, re_kyc, or reactivation. You MUST return this exact string in your JSON response under extracted_data. Example: {"ui_action": "RENDER_KYC_UPLOAD", "agent_message": "...", "extracted_data": {"account_type": "retail_savings"}}.
- If the user's intent is 're_kyc' or 'reactivation', you must treat this as a lifecycle flow. Returning 'RENDER_CHAT' and asking for an Account ID if not already provided is acceptable.
- You EXPLICITLY refuse any question or command not related to bank onboarding or KYC.
- Always return a JSON format with EXACTLY the specified keys.

Valid 'ui_action' strings the frontend explicitly expects:
- "RENDER_CHAT" (Normal conversation)
- "RENDER_PHONE_AUTH" (Display phone)
- "RENDER_EMAIL_AUTH" (Display email)
- "RENDER_KYC_UPLOAD" (Upload PAN/Aadhaar)
- "RENDER_DATA_REVIEW" (Edit Form for OCR Data)
- "RENDER_FACE_VERIFICATION" (Blink verification and Selfie capture)
- "RENDER_AUTO_APPROVE" (Success Green Screen)
- "RENDER_HUMAN_REVIEW" (Yellow Warning Screen)
- "RENDER_AUTO_REJECT" (Red Auto Reject Screen)
- "RENDER_ERROR" (General error display screen)

JSON Schema:
{
  "ui_action": "...",
  "agent_message": "...",
  "data_required": ["phone", "email", "intent"],
  "session_ulid": "01KK...",
  "extracted_data": {"account_type": "retail_savings", "name": "..."} // Output specific extracted fields like account_type here.
}
"""

async def handle_tool_call(call: Any, session_ulid: str, db: AsyncSession, final_data: Optional[Dict[str, Any]] = None) -> dict:
    """Executes the specific Python function safely and returns the result dictionary."""
    name = call.name
    args = call.args
    
    logger.info(f"LLM Orchestrator triggered Tool: {name} with {args}")
    
    try:
        if name == "trigger_phone_otp":
            res = await send_phone_otp(args["phone"])
            return {"status": "success", "message": f"OTP successfully dispatched to {args['phone']}", "session_ulid": res["session_ulid"]}
            
        elif name == "trigger_email_otp":
            await send_email_otp(args["email"], pending_session_id=(session_ulid or args.get("pending_session_id")))
            return {"status": "success", "message": f"OTP successfully dispatched to {args['email']}"}
            
        elif name == "submit_phone_otp":
            res = await verify_phone_otp(args["phone"], args["code"], pending_session_id=session_ulid)
            return {"status": "success", "message": "Phone verified successfully.", "session_ulid": res["pending_session_id"]}
            
        elif name == "submit_email_otp":
            res = await verify_email_otp(args["code"], pending_session_id=session_ulid)
            from app.agents.entry_agent import register_user
            user = await register_user(db, phone=res["phone"], email=res["email"])
            await redis_client.delete(f"pending_auth:{session_ulid}")
            return {"status": "success", "message": "Email verified and user successfully registered. Proceeding to Account Type Selection.", "session_ulid": user.id}
            
        elif name == "classify_user_intent":
            res = await classify_intent(args["user_message"])
            if session_ulid:
                # ── Duplicate Intent Check ──
                user_res = await db.execute(select(UserInitial).where(UserInitial.id == session_ulid))
                current_user = user_res.scalar_one_or_none()
                if current_user and current_user.phone:
                    duplicate_check = await db.execute(
                        select(UserInitial).where(
                            (UserInitial.phone == current_user.phone) &
                            (UserInitial.account_type == res.intent.value) &
                            (UserInitial.status.in_(['FINALIZED', 'APPROVED', 'approved', 'FACE_VERIFIED', 'MANUAL_REVIEW', 'UNDER_REVIEW']))
                        )
                    )
                    if duplicate_check.scalar_one_or_none():
                        return {
                            "ui_action": "RENDER_CHAT",
                            "status": "error",
                            "agent_message": f"You already have an existing {res.intent.value.replace('_', ' ').title()} account. You cannot open a duplicate account of the same type.",
                            "data_required": ["intent"],
                            "session_ulid": session_ulid
                        }
                        
                await redis_client.setex(f"session_intent:{session_ulid}", 86400, res.intent.value)
            return {"status": "success", "intent": res.intent.value, "documents_required": res.checklist}
            
        elif name == "request_document_upload":
            return {"status": "success", "message": "UI signaled to open document upload drawer."}
            
        elif name == "extract_and_review_tool":
            from loguru import logger
            
            try:
                # Query uploaded file URLs for this session
                from app.db.models.document import UserDocument
                from sqlalchemy.future import select as sa_select
                stmt = sa_select(UserDocument.file_url).where(UserDocument.session_id == session_ulid)
                result = await db.execute(stmt)
                list_of_urls = [row[0] for row in result.all()]
                
                logger.info(f"[OnboardAI][TOOL] extract_and_review_tool: {len(list_of_urls)} files for {session_ulid}")
                
                # Route to the correct Celery task based on session intent
                # This mirrors the Fast Path routing in orchestrate_session
                from app.db.redis_client import redis_client
                intent_raw = await redis_client.get(f"session_intent:{session_ulid}")
                if not intent_raw:
                    session_intent = "retail_savings"
                elif isinstance(intent_raw, bytes):
                    session_intent = intent_raw.decode('utf-8')
                else:
                    session_intent = str(intent_raw)
                
                # Check for lifecycle (Re-KYC / Reactivation) override
                lifecycle_raw = await redis_client.get(f"lifecycle_flag:{session_ulid}")
                if lifecycle_raw:
                    lifecycle_intent = lifecycle_raw.decode('utf-8') if isinstance(lifecycle_raw, bytes) else str(lifecycle_raw)
                    # Re-KYC and Reactivation use the same document pipeline as
                    # the account_type stored from the original onboarding
                    lc_account_raw = await redis_client.get(f"lifecycle_account_type:{session_ulid}")
                    if lc_account_raw:
                        session_intent = lc_account_raw.decode('utf-8') if isinstance(lc_account_raw, bytes) else str(lc_account_raw)
                    logger.info(f"[OnboardAI][TOOL] Lifecycle flow detected ({lifecycle_intent}), routing as intent={session_intent}")
                
                if session_intent == "sme_current":
                    from app.workers.tasks.extraction import process_sme_documents_async
                    process_sme_documents_async.delay(session_ulid, list_of_urls)
                else:
                    from app.workers.tasks.extraction import process_documents_async
                    process_documents_async.delay(session_ulid, minio_paths=list_of_urls)
                    
                # ── Stage 1 Pre-Warm: dispatch face model loading NOW ────────────────────
                try:
                    from app.workers.tasks.face_verification_tasks import prewarm_face_models
                    prewarm_face_models.apply_async(
                        args=[session_ulid],
                        queue="face_verification"
                    )
                    logger.info(f"[OnboardAI][PREWARM] Stage 1: face models pre-warm dispatched for {session_ulid}")
                except Exception as pw_err:
                    logger.warning(f"[OnboardAI][PREWARM] Stage 1 dispatch failed (non-fatal): {pw_err}")

                return {
                    "ui_action": "RENDER_PROCESSING",
                    "status": "processing",
                    "message": "Documents sent for background extraction."
                }
            except Exception as fallback_err:
                logger.exception(f"[OnboardAI][CRITICAL] Error natively queueing Celery Task: {fallback_err}")
                return {
                    "ui_action": "RENDER_ERROR",
                    "agent_message": "Failed to queue extraction natively.",
                    "data_required": [],
                    "session_ulid": session_ulid
                }
            
        elif name == "trigger_face_verification_tool":
            # Stage 2 Pre-Warm: camera page is opening — ensure models are warm.
            # Idempotent: if Stage 1 already completed, this is a no-op in the worker.
            try:
                from app.workers.tasks.face_verification_tasks import prewarm_face_models
                prewarm_face_models.apply_async(
                    args=[session_ulid],
                    queue="face_verification"
                )
                logger.info(f"[OnboardAI][PREWARM] Stage 2: face models pre-warm dispatched for {session_ulid}")
            except Exception as pw_err:
                logger.warning(f"[OnboardAI][PREWARM] Stage 2 dispatch failed (non-fatal): {pw_err}")
            return {"status": "success", "message": "UI signaled to open face verification capture."}

        elif name == "execute_hybrid_freeze_tool":
            if not final_data:
                return {
                    "ui_action": "RENDER_ERROR",
                    "agent_message": "final_data JSON payload is missing.",
                    "data_required": [],
                    "session_ulid": session_ulid
                }
            
            result = await db.execute(select(UserInitial).where(UserInitial.id == session_ulid))
            user = result.scalar_one_or_none()
            
            if not user:
                return {
                    "ui_action": "RENDER_ERROR",
                    "agent_message": "Critical: Session ULID not found in database for final freeze.",
                    "data_required": [],
                    "session_ulid": session_ulid
                }
                
            # 1. Inject the user verified edits
            user.verified_data = final_data
            
            # 2. Save verified data changes
            try:
                await db.commit()
            except Exception as e:
                logger.error(f"Database commit failed: {e}")
                return {
                    "ui_action": "RENDER_ERROR",
                    "agent_message": f"Database commit failed: {e}",
                    "data_required": [],
                    "session_ulid": session_ulid
                }
            
            # 3. Simulate Basic Risk Rulebook
            risk_score = 0 
            if not user.name or not user.pan_id:
                risk_score = 50
                
            return {"status": "success", "risk_score": risk_score, "is_approved": risk_score < 30}
            
        elif name == "escalate_to_human_tool":
            result = await db.execute(select(UserInitial).where(UserInitial.id == session_ulid))
            user = result.scalar_one_or_none()
            if user:
                user.status = "UNDER_REVIEW"
                await db.commit()
            return {"status": "success", "message": "User pushed to Ops Queue natively."}
            
        elif name == "search_similar_cases":
            results = await query_similar_cases(args.get("context", ""), db)
            return {"status": "success", "similar_cases_found": results}
            
        else:
            return {
                "ui_action": "RENDER_ERROR",
                "agent_message": "Unknown tool executed natively.",
                "data_required": [],
                "session_ulid": session_ulid
            }
            
    except Exception as e:
        return {
            "ui_action": "RENDER_ERROR",
            "agent_message": str(e),
            "data_required": [],
            "session_ulid": session_ulid
        }

async def orchestrate_session(message: str, session_ulid: Optional[str], current_state: dict, db: AsyncSession, source: Optional[str] = None, final_data: Optional[Dict[str, Any]] = None) -> dict:
    """
    The Engine Loop:
    1. Grabs dynamic state.
    2. Builds the context.
    3. Triggers Gemini.
    4. Executes requested tools in Python.
    5. Returns strictly formatted JSON JSON UI.
    """
    # ── LAYER 1: DETERMINISTIC FAST PATH (Absolute Priority) ──────────────────
    # Detects machine-generated signals (OTP triggers, system events) via regex
    # BEFORE any expensive setup.  On a hit we set `source` to the handler name
    # that existing if-blocks already check, and inject extracted data (OTP codes,
    # account IDs) into `current_state`.  This guarantees the handler fires AND
    # returns before the SLOW PATH Gemini LLM block is ever reached.
    from app.agents.fast_path_router import try_fast_path
    _fast_hit = try_fast_path(message, current_state)
    if _fast_hit:
        _fp_route = _fast_hit["route"]
        _fp_extracted = _fast_hit.get("extracted", {})
        logger.info(f"[OnboardAI][FAST_PATH] ⚡ Layer 1 hit: route={_fp_route} | extracted={_fp_extracted}")

        # ── Route → source mapping ───────────────────────────────────────────
        # Maps fast-path route names to the `source` values that the existing
        # handler if-blocks already check.  This is the ONLY bridge between
        # Layer 1 detection and Layer 2 execution — no handler logic is
        # duplicated.
        _ROUTE_TO_SOURCE = {
            "trigger_otp_send":          "phone_send_otp",
            "trigger_email_otp_send":    "email_send_otp",
            "phone_otp_submit":          "phone_verify_otp",
            "email_otp_submit":          "email_verify_otp",
            "face_verification_success": "face_verification_success",
            "submit_additional_info":    "submit_additional_info",
            "lifecycle_account_id":      "lifecycle_account_id",
        }
        _mapped_source = _ROUTE_TO_SOURCE.get(_fp_route)
        if _mapped_source:
            source = _mapped_source
            logger.info(f"[OnboardAI][FAST_PATH] source overridden: '{_fp_route}' → '{source}'")

        # ── Inject extracted fields into current_state ────────────────────────
        # OTP codes, account IDs, etc. must be available to the handlers via
        # current_state, since that's where they read them from.
        if isinstance(current_state, dict) and _fp_extracted:
            if "otp_code" in _fp_extracted:
                current_state["otp"]  = _fp_extracted["otp_code"]
                current_state["code"] = _fp_extracted["otp_code"]
            if "account_id" in _fp_extracted:
                current_state["account_id"] = _fp_extracted["account_id"]

    # ── ROUTING LOG ────────────────────────────────────────────────────────────
    session_ulid = session_ulid or str(ULID())
    logger.info(f"[OnboardAI][ROUTER] Routing message: {message[:80]!r} | source={source} | session={session_ulid}")
    # ── FAST PATH: Lifecycle Init (Re-KYC / Reactivation) ────────────────────
    # Task 2: Intercept BEFORE everything else. Account ID becomes the session_ulid.
    # Task 6: Redis flag forces state evaluator to ignore DB status.
    if source == "lifecycle_init":
        from app.agents.lifecycle_agent import LifecycleOrchestrator
        account_id = current_state.get("account_id") or message.strip()
        intent_val = current_state.get("lifecycle_intent", "re_kyc")
        logger.info(f"[OnboardAI][LIFECYCLE] Init for account_id={account_id} intent={intent_val}")

        try:
            lc = LifecycleOrchestrator(intent=intent_val)
        except ValueError:
            lc = LifecycleOrchestrator(intent="re_kyc")

        user = await lc.lookup_account(account_id, db)
        if not user:
            return lc.get_not_found_response(account_id)

        # Task 2: Hijack session — use account_id as the session_ulid
        session_ulid = account_id

        # ── Task 1: Automated Intent Anchoring ────────────────────────────────
        # Read the account_type already stored in the DB. Cache it immediately so
        # the email-verify step can skip the intent question and jump to doc upload.
        _db_account_type = user.account_type or "retail_savings"
        await redis_client.setex(f"lifecycle_account_type:{session_ulid}", 86400, _db_account_type)
        logger.info(f"[OnboardAI][LIFECYCLE] Anchored intent='{_db_account_type}' from DB for {session_ulid}")

        # ── Task 4: Mark session as RE_KYC_PENDING ────────────────────────────
        await LifecycleOrchestrator.upsert_user_data(
            session_ulid,
            {"status": "RE_KYC_PENDING" if lc.intent == 're_kyc' else "REACTIVATION_PENDING"},
            db
        )
        logger.info(f"[OnboardAI][LIFECYCLE] Status set to RE_KYC_PENDING for {session_ulid}")

        # Task 6: Set Redis flag so the state evaluator starts fresh
        await LifecycleOrchestrator.set_lifecycle_flag(session_ulid, lc.intent)
        # Task 7: Return masked phone in the action response
        return lc.get_initial_action(user)

    # Standard Gemini chat has a timeout risk if the orchestrator blocks waiting
    # for a Celery task. We intercept this signal HERE before any LLM call,
    # fire-and-forget the background task, and immediately return RENDER_PROCESSING.
    if source == "kyc_upload" or "SYSTEM: DOCUMENTS_UPLOADED_SUCCESSFULLY" in message:
        logger.info(f"[OnboardAI][ROUTER] ⚡ FAST PATH: Firing Celery task for session {session_ulid}")
        try:
            # 1. Query the UserDocuments table for the session's uploaded files
            stmt = select(UserDocument.file_url).where(UserDocument.session_id == session_ulid)
            result = await db.execute(stmt)
            list_of_urls = [row[0] for row in result.all()]

            logger.info(f"[OnboardAI][ROUTER] Found {len(list_of_urls)} files to process for {session_ulid}")

            # 2. Route to SME (intent based) or Retail
            intent_raw = await redis_client.get(f"session_intent:{session_ulid}")
            if not intent_raw:
                session_intent = "retail_savings"
            elif isinstance(intent_raw, bytes):
                session_intent = intent_raw.decode('utf-8')
            else:
                session_intent = str(intent_raw)
            
            # 2b. Lifecycle override: Re-KYC / Reactivation sessions carry the
            # original account_type from the DB, cached under lifecycle_account_type.
            lifecycle_raw = await redis_client.get(f"lifecycle_flag:{session_ulid}")
            if lifecycle_raw:
                lc_account_raw = await redis_client.get(f"lifecycle_account_type:{session_ulid}")
                if lc_account_raw:
                    session_intent = lc_account_raw.decode('utf-8') if isinstance(lc_account_raw, bytes) else str(lc_account_raw)
                _lc_label = lifecycle_raw.decode('utf-8') if isinstance(lifecycle_raw, bytes) else str(lifecycle_raw)
                logger.info(f"[OnboardAI][ROUTER] Lifecycle flow ({_lc_label}) detected, routing as intent={session_intent}")
            
            if session_intent == "sme_current":
                logger.info(f"[OnboardAI][ROUTER] Detected SME flow (intent={session_intent}). Dispatching process_sme_documents_async.")
                from app.workers.tasks.extraction import process_sme_documents_async
                process_sme_documents_async.delay(session_ulid, list_of_urls)
            else:
                logger.info(f"[OnboardAI][ROUTER] Detected Retail flow (intent={session_intent}). Dispatching process_documents_async.")
                from app.workers.tasks.extraction import process_documents_async
                process_documents_async.delay(session_ulid, minio_paths=list_of_urls)

            # ── Stage 1 Pre-Warm: dispatch face model loading NOW ────────────────────
            # The user will spend ~30–60s reviewing OCR extracted data.
            # We use that idle time to load DeepFace + MediaPipe into the
            # face_verification worker so the actual verification is instant.
            try:
                from app.workers.tasks.face_verification_tasks import prewarm_face_models
                prewarm_face_models.apply_async(
                    args=[session_ulid],
                    queue="face_verification"
                )
                logger.info(f"[OnboardAI][PREWARM] Stage 1: face models pre-warm dispatched for {session_ulid}")
            except Exception as pw_err:
                logger.warning(f"[OnboardAI][PREWARM] Stage 1 dispatch failed (non-fatal): {pw_err}")

            logger.info(f"[OnboardAI][ROUTER] ✓ Celery task dispatched, returning RENDER_PROCESSING immediately")
            return {
                "ui_action": "RENDER_PROCESSING",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": "Your documents are being processed. Please wait a moment..."
            }
        except Exception as celery_err:
            logger.exception(f"[OnboardAI][FAST PATH] Celery dispatch failed: {celery_err}")
            return {
                "ui_action": "RENDER_KYC_UPLOAD",
                "extracted_data": {},
                "agent_message": f"Failed to process documents: {str(celery_err)}. Please try uploading again."
            }

    # ── FAST PATH: Status Polling ─────────────────────────────────────────────
    if source == "poll" or "SYSTEM: POLL_STATUS" in message:
        from app.db.redis_client import get_temp_extraction
        logger.info(f"[OnboardAI][POLL] Received poll request for session: {session_ulid}")
        
        temp_data = get_temp_extraction(session_ulid)
        if not temp_data:
            logger.info(f"[OnboardAI][POLL] ... No data found in Redis for {session_ulid} yet.")
            return {
                "ui_action": "RENDER_PROCESSING",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": "Processing your documents... please wait."
            }

        logger.info(f"[OnboardAI][POLL] ✓ Data found in Redis for {session_ulid}. Keys: {list(temp_data.keys())}")
        
        if "validation" in temp_data:
            combined_data = temp_data["validation"].get("combined_data", {})
            if combined_data:
                # Task 2: Synchronize State to PostgreSQL immediately on SUCCESS
                logger.info(f"[OnboardAI][POLL] Checking Database for session: {session_ulid}")
                stmt = select(UserInitial).where(UserInitial.id == session_ulid)
                result = await db.execute(stmt)
                user = result.scalar_one_or_none()
                
                if user:
                    logger.info(f"[OnboardAI][POLL] ✓ User found in DB. Synchronizing {list(combined_data.keys())} to PostgreSQL.")
                    
                    # 1. Store the raw JSON blob
                    user.verified_data = combined_data
                    
                    # 2. Flatten relevant fields into columns
                    user.name = combined_data.get("name")
                    user.father_name = combined_data.get("father_name")
                    user.dob = combined_data.get("dob")
                    user.address = combined_data.get("address")
                    
                    if "pan_id" in combined_data:
                        user.pan_id = combined_data.get("pan_id")
                    if "aadhar_id" in combined_data:
                        user.aadhar_id = combined_data.get("aadhar_id")
                    
                    # 3. Explicitly signal change to SQLAlchemy
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(user, "verified_data")
                    
                    # 4. Advance status
                    user.status = "KYC_UPLOADED"
                    
                    try:
                        await db.commit()
                        logger.info(f"[OnboardAI][POLL] ✓ PostgreSQL Sync SUCCESS for {session_ulid}. Status: {user.status}")
                    except Exception as commit_err:
                        logger.info(f"[OnboardAI][POLL] ✗ ERROR during DB commit: {commit_err}")
                        await db.rollback()
                        raise commit_err
                    
                    # Task 3: Format the Payload for RENDER_DATA_REVIEW
                    # Pass the FULL validation dict so gst_data reaches the frontend
                    validation_block = temp_data["validation"]
                    full_extracted = {
                        "combined_data": dict(combined_data),
                        "gst_data":      dict(validation_block.get("gst_data", {})),
                        "valid":          bool(validation_block.get("valid", True)),
                        "flags":          list(validation_block.get("flags", [])),
                    }
                    return {
                        "ui_action": "RENDER_DATA_REVIEW",
                        "session_ulid": session_ulid,
                        "data_required": [],
                        "agent_message": "Your document data has been extracted. Please review and confirm below.",
                        "extracted_data": full_extracted
                    }
                else:
                    logger.info(f"[OnboardAI][POLL] ✗ CRITICAL: Session {session_ulid} NOT found in database.")
            else:
                logger.info(f"[OnboardAI][POLL] ! combined_data is empty in Redis for {session_ulid}")
        else:
            logger.info(f"[OnboardAI][POLL] ! validation key missing in temp_data for {session_ulid}")
        
        # Fallback if found but not ready
        return {
            "ui_action": "RENDER_PROCESSING",
            "session_ulid": session_ulid,
            "data_required": [],
            "agent_message": "Processing your documents... please wait."
        }

    # ── FAST PATH: Face Verification Polling ──────────────────────────────────
    if source == "face_poll" or "SYSTEM: POLL_FACE_VERIFICATION" in message:
        logger.info(f"[OnboardAI][POLL_FACE] Received poll request for session: {session_ulid}")
        face_res_raw = await redis_client.get(f"face_verification:{session_ulid}")
        
        if not face_res_raw:
            return {
                "ui_action": "RENDER_PROCESSING",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": "Verifying your identity... please stay on camera."
            }
            
        face_result = json.loads(face_res_raw)
        if face_result.get("status") == "success" and face_result.get("overall_verdict") is True:
            # ── Optimization: Direct SQL Update ──
            # Using a direct update query is more performant and avoids InterfaceErrors 
            # associated with overlapping selects/updates on the same session.
            logger.info(f"[OnboardAI][POLL_FACE] ✓ Verification SUCCESS. Syncing to DB via Direct Update.")
            stmt = (
                update(UserInitial)
                .where(UserInitial.id == session_ulid)
                .values(face_verified=True, status="FACE_VERIFIED")
            )
            await db.execute(stmt)
            await db.commit()
            logger.info(f"[OnboardAI][POLL_FACE] ✓ DB Sync Complete.")

            # Route to Additional Info for ALL account types that require it.
            # SME gets a business-specific message; retail gets a generic one.
            # The form schema itself is determined by get_form_schema(account_type).
            account_type = user.account_type if user else None
            is_sme = account_type == "sme_current"

            from app.services.additional_info_service import get_form_schema
            schema = get_form_schema(account_type or "retail_savings")

            if is_sme:
                info_msg = (
                    "Identity verified! To complete your SME Current Account opening, "
                    "please provide your business details below."
                )
            else:
                info_msg = (
                    "Identity verified! Please complete the final regulatory details "
                    "below to finish your account opening."
                )

            # ── Lifecycle: use RENDER_ADDITIONAL_INFO (unified form action) ──
            from app.agents.lifecycle_agent import LifecycleOrchestrator
            _face_lc_flag = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid)
            _face_ui_action = "RENDER_ADDITIONAL_INFO" if _face_lc_flag else "RENDER_ADDITIONAL_INFO_FORM"

            return {
                "ui_action": _face_ui_action,
                "session_ulid": session_ulid,
                "data_required": schema,
                "agent_message": info_msg,
                "extracted_data": {
                    **face_result,
                    "form_schema": schema,
                    "account_type": account_type,
                    "lifecycle_intent": _face_lc_flag
                }
            }
        elif face_result.get("status") == "error":
            return {
                "ui_action": "RENDER_FACE_VERIFICATION",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": f"Verification failed: {face_result.get('message')}. Please try again."
            }
            
        return {
            "ui_action": "RENDER_PROCESSING",
            "session_ulid": session_ulid,
            "data_required": [],
            "agent_message": "Verifying your identity... please stay on camera."
        }

    # ── FAST PATH: User Confirmed Extracted Data (KYC + optional GST) ─────────
    if "USER_CONFIRMED_DATA" in message:
        logger.info(f"[OnboardAI][CONFIRMED_DATA] Fast-path triggered for session: {session_ulid}")
        try:
            from app.db.models.user import AdditionalInfo
            import uuid

            kyc   = final_data.get("kyc_data", final_data) if final_data else {}
            gst   = final_data.get("gst_data", {}) if final_data else {}

            # 1. Upsert KYC fields into user_initial
            stmt = select(UserInitial).where(UserInitial.id == session_ulid)
            res  = await db.execute(stmt)
            user = res.scalar_one_or_none()
            if not user:
                return {
                    "ui_action": "RENDER_ERROR",
                    "agent_message": "Session not found. Please restart onboarding.",
                    "data_required": [],
                    "session_ulid": session_ulid
                }

            user.name         = kyc.get("name")        or user.name
            user.father_name  = kyc.get("father_name") or user.father_name
            user.dob          = kyc.get("dob")         or user.dob
            user.address      = kyc.get("address")     or user.address
            user.pan_id       = kyc.get("pan_id")      or user.pan_id
            user.aadhar_id    = kyc.get("aadhar_id")   or user.aadhar_id
            user.account_type = "sme_current" if gst else (user.account_type or "retail_savings")
            user.status       = "KYC_CONFIRMED"

            # 2. Upsert GST data into additional_info (only for SME)
            if gst:
                info_stmt = select(AdditionalInfo).where(AdditionalInfo.session_ulid == session_ulid)
                info_res  = await db.execute(info_stmt)
                existing_info = info_res.scalar_one_or_none()
                if existing_info:
                    existing_info.data = gst
                else:
                    db.add(AdditionalInfo(
                        id=str(uuid.uuid4()),
                        session_ulid=session_ulid,
                        data=gst
                    ))

            await db.commit()
            logger.info(f"[OnboardAI][CONFIRMED_DATA] ✓ DB committed. account_type={user.account_type}, GST={'yes' if gst else 'no'}")

            return {
                "ui_action": "RENDER_FACE_VERIFICATION",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": "Data confirmed! Please complete face verification to proceed."
            }
        except Exception as e:
            await db.rollback()
            logger.info(f"[OnboardAI][CONFIRMED_DATA] ✗ ERROR: {e}")
            return {
                "ui_action": "RENDER_ERROR",
                "agent_message": f"Failed to confirm data: {str(e)}",
                "data_required": [],
                "session_ulid": session_ulid
            }

    # ── FAST PATH: Handle Additional Info Submission ─────────────────────────
    if source == "submit_additional_info" or message == "SYSTEM: SUBMIT_ADDITIONAL_INFO":
        logger.info(f"[OnboardAI][ADD_INFO] Received form submission for session: {session_ulid}")
        if not final_data:
            return {
                "ui_action": "RENDER_ERROR",
                "agent_message": "Additional Information payload (final_data) is missing.",
                "data_required": [],
                "session_ulid": session_ulid
            }
            
        from app.db.models.user import AdditionalInfo
        from app.db.models.bank_branch import BankBranch
        import uuid
        import random

        # ── Helper: generate unique 10-digit account number ──────────────────
        def _generate_account_number() -> str:
            return str(random.randint(1_000_000_000, 9_999_999_999))

        # ── Extract pref_branch from final_data (remove so it's not in JSON blob) ──
        _pref_branch_ifsc = final_data.pop("pref_branch", None) or final_data.pop("Pref_branch", None)

        # 1. Check for existing info for idempotency (upsert)
        stmt = select(AdditionalInfo).where(AdditionalInfo.session_ulid == session_ulid)
        res = await db.execute(stmt)
        existing_info = res.scalar_one_or_none()
        
        if existing_info:
            logger.info(f"[OnboardAI][ADD_INFO] Merging into existing record for {session_ulid}")
            # Safe merge: spread existing data first, then overlay with incoming fields.
            # This preserves keys like 'gst_data' written during document extraction
            # and only updates or adds the keys the user submitted in the form.
            existing_dict = existing_info.data if isinstance(existing_info.data, dict) else {}
            merged = {**existing_dict, **final_data}
            existing_info.data = merged
            if _pref_branch_ifsc:
                existing_info.pref_branch = _pref_branch_ifsc
            # Explicitly signal change to SQLAlchemy for JSONB mutation
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(existing_info, "data")
        else:
            logger.info(f"[OnboardAI][ADD_INFO] Creating new record for {session_ulid}")
            new_info = AdditionalInfo(
                id=str(uuid.uuid4()),
                session_ulid=session_ulid,
                data=final_data,
                pref_branch=_pref_branch_ifsc,
            )
            db.add(new_info)
        
        try:
            # 2. Update UserInitial status and use AdditionalInfoService for lifecycle
            result = await db.execute(select(UserInitial).where(UserInitial.id == session_ulid))
            user_obj = result.scalar_one_or_none()
            
            # ── Lifecycle Detection ──────────────────────────────────────────
            from app.agents.lifecycle_agent import LifecycleOrchestrator
            _lc_flag_final = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid)

            if _lc_flag_final:
                # ── Lifecycle: Use AdditionalInfoService (Task 2) ───────────
                logger.info(f"[OnboardAI][ADD_INFO][LIFECYCLE] Using AdditionalInfoService for {session_ulid}")
                from app.services.additional_info_service import update_additional_info
                await update_additional_info(session_ulid, final_data, db)
                
                # Update status to UNDER_REVIEW
                if user_obj:
                    user_obj.status = "UNDER_REVIEW"
                    await db.commit()
                
                # Clear lifecycle Redis flags — this is the final step
                await LifecycleOrchestrator.clear_lifecycle_flag(session_ulid)
                await redis_client.delete(f"lifecycle_account_type:{session_ulid}")
                _lc_label_final = _lc_flag_final.replace("_", " ").title()
                logger.info(f"[OnboardAI][LIFECYCLE] {_lc_label_final} fully complete. Lifecycle flags cleared.")

                # ── Phase 4 Hook: Risk evaluation BEFORE final success (Re-KYC / Reactivation) ──
                _fv_raw_lc = await redis_client.get(f"face_verification:{session_ulid}")
                _fv_res_lc = json.loads(_fv_raw_lc) if _fv_raw_lc else {}
                _risk_routing = await _apply_risk_routing(
                    user=user_obj, db=db, session_ulid=session_ulid, final_data=final_data,
                    face_result=_fv_res_lc, current_state=current_state
                )
                if _risk_routing["_risk_action"] != "PROCEED":
                    return _risk_routing

                # ── Task 3: Return RENDER_SUCCESS (not RENDER_AUTO_APPROVE) ──
                return {
                    "ui_action": "RENDER_SUCCESS",
                    "session_ulid": session_ulid,
                    "data_required": [],
                    "agent_message": (
                        f"Your profile has been successfully updated and your account is now active. "
                        f"Your {_lc_label_final} request has been completed."
                    )
                }
            else:
                # ── Standard Onboarding: existing upsert logic ───────────────
                if user_obj:
                    logger.info(f"[OnboardAI][ADD_INFO] Setting session {session_ulid} to UNDER_REVIEW")
                    user_obj.status = "UNDER_REVIEW"
                await db.commit()
                logger.info(f"[OnboardAI][ADD_INFO] ✓ Successfully saved additional info and updated status for {session_ulid}")

                # ── Phase 4 Hook: Risk evaluation BEFORE final success (Retail / SME) ──
                _fv_raw_std = await redis_client.get(f"face_verification:{session_ulid}")
                _fv_res_std = json.loads(_fv_raw_std) if _fv_raw_std else {}
                _risk_routing = await _apply_risk_routing(
                    user=user_obj, db=db, session_ulid=session_ulid, final_data=final_data,
                    face_result=_fv_res_std, current_state=current_state
                )
                if _risk_routing["_risk_action"] != "PROCEED":
                    return _risk_routing

                # ── AUTO APPROVE: generate account number + fetch branch details ──
                _acct_no = _generate_account_number()

                # Save account number to AdditionalInfo
                try:
                    _stmt_info = select(AdditionalInfo).where(AdditionalInfo.session_ulid == session_ulid)
                    _res_info = await db.execute(_stmt_info)
                    _saved_info = _res_info.scalar_one_or_none()
                    if _saved_info:
                        _saved_info.account_number = _acct_no
                        await db.commit()
                        logger.info(f"[OnboardAI][ADD_INFO] Account number {_acct_no} saved for {session_ulid}")
                except Exception as _acct_err:
                    logger.warning(f"[OnboardAI][ADD_INFO] Non-fatal: failed to save account_number: {_acct_err}")

                # Fetch branch details for Complete screen
                _branch_data: dict = {}
                if _pref_branch_ifsc:
                    try:
                        _br_res = await db.execute(
                            select(BankBranch).where(BankBranch.ifsc == _pref_branch_ifsc)
                        )
                        _branch = _br_res.scalars().first()
                        if _branch:
                            _branch_data = {
                                "pref_branch_ifsc":     _branch.ifsc,
                                "branch_name":          _branch.branch_name,
                                "branch_address":       _branch.branch_address,
                                "manager_name":         _branch.manager_name,
                                "relationship_officer": _branch.relationship_officer,
                            }
                    except Exception as _br_err:
                        logger.warning(f"[OnboardAI][ADD_INFO] Non-fatal: branch lookup failed: {_br_err}")

                # Build extracted_data for frontend Complete screen
                _complete_data = {
                    "account_number": _acct_no,
                    "account_type":   getattr(user_obj, "account_type", "") if user_obj else "",
                    "name":           getattr(user_obj, "name", "") if user_obj else "",
                    "father_name":    getattr(user_obj, "father_name", "") if user_obj else "",
                    **_branch_data,
                }

                # ── Fire approval email (fire-and-forget) ────────────────────
                if user_obj and user_obj.email:
                    try:
                        from app.services.email_service import send_approval_email
                        asyncio.create_task(send_approval_email(
                            email=user_obj.email,
                            name=user_obj.name or "",
                            application_id=session_ulid,
                            account_number=_acct_no,
                            branch_name=_branch_data.get("branch_name", ""),
                            branch_address=_branch_data.get("branch_address", ""),
                            ifsc=_branch_data.get("pref_branch_ifsc", _pref_branch_ifsc or ""),
                            account_type=user_obj.account_type or "",
                            manager_name=_branch_data.get("manager_name"),
                            relationship_officer=_branch_data.get("relationship_officer"),
                        ))
                        logger.info(f"[OnboardAI][EMAIL] Approval email task queued for {session_ulid}")
                    except Exception as _email_err:
                        logger.warning(f"[OnboardAI][EMAIL] Non-fatal: approval email task failed: {_email_err}")

                return {
                    "ui_action": "RENDER_AUTO_APPROVE",
                    "session_ulid": session_ulid,
                    "data_required": [],
                    "agent_message": "All data collected successfully. Processing final approval...",
                    "extracted_data": _complete_data,
                }
        except Exception as e:
            await db.rollback()
            logger.info(f"[OnboardAI][ADD_INFO] ✗ ERROR saving additional info: {e}")
            return {
                "ui_action": "RENDER_ERROR",
                "agent_message": f"Failed to save additional information: {str(e)}",
                "data_required": [],
                "session_ulid": session_ulid
            }

    # ── FAST PATH: Face Verification Success (Special handling for Digital-Only) ──
    if source == "face_verification_success" or "SYSTEM: FACE_VERIFICATION_SUCCESSFUL" in message:
        result = await db.execute(select(UserInitial).where(UserInitial.id == session_ulid))
        user = result.scalar_one_or_none()
        
        if user:
            user.face_verified = True
            await db.commit()

            # ── Task 4: Lifecycle Face Success Routing ────────────────────────
            # Retail and SME accounts MUST continue to Additional Info after face.
            # Digital-Only bypasses Additional Info (Task 5 fast-path).
            from app.agents.lifecycle_agent import LifecycleOrchestrator
            _lc_flag = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid)
            if _lc_flag:
                _lc_label = _lc_flag.replace("_", " ").title()
                _acct_type = user.account_type or "retail_savings"
                # Persist face_verified via upsert (Task 6 — SQL UPDATE, not ORM)
                await LifecycleOrchestrator.upsert_user_data(
                    session_ulid,
                    {"face_verified": True},
                    db
                )

                if _acct_type == "digital_only":
                    # ── Task 5: Digital-Only Fast-Path ──────────────────────
                    # No additional info needed — finalize immediately.
                    await LifecycleOrchestrator.upsert_user_data(
                        session_ulid, {"status": "UNDER_REVIEW"}, db
                    )
                    await LifecycleOrchestrator.clear_lifecycle_flag(session_ulid)
                    await redis_client.delete(f"lifecycle_account_type:{session_ulid}")
                    logger.info(f"[OnboardAI][LIFECYCLE] digital_only {_lc_label} complete. → UNDER_REVIEW")
                    
                    from app.db.models.user import AdditionalInfo
                    from app.db.models.bank_branch import BankBranch
                    import uuid
                    import random
                    
                    def _generate_account_number() -> str:
                        return str(random.randint(1_000_000_000, 9_999_999_999))
                        
                    _acct_no = _generate_account_number()
                    
                    # Upsert AdditionalInfo for digital with default branch and generated account number
                    _stmt_info = select(AdditionalInfo).where(AdditionalInfo.session_ulid == session_ulid)
                    _res_info = await db.execute(_stmt_info)
                    _existing_info = _res_info.scalar_one_or_none()
                    
                    if _existing_info:
                        _existing_info.pref_branch = "CDBB0008863"
                        _existing_info.account_number = _acct_no
                    else:
                        _new_info = AdditionalInfo(
                            id=str(uuid.uuid4()),
                            session_ulid=session_ulid,
                            data={},
                            pref_branch="CDBB0008863",
                            account_number=_acct_no
                        )
                        db.add(_new_info)
                    
                    await db.commit()
                    
                    # Fetch branch details for email payload
                    _branch_data: dict = {}
                    try:
                        _br_res = await db.execute(select(BankBranch).where(BankBranch.ifsc == "CDBB0008863"))
                        _branch = _br_res.scalars().first()
                        if _branch:
                            _branch_data = {
                                "pref_branch_ifsc": _branch.ifsc,
                                "branch_name": _branch.branch_name,
                                "branch_address": _branch.branch_address,
                                "manager_name": _branch.manager_name,
                                "relationship_officer": _branch.relationship_officer,
                            }
                    except Exception as _br_err:
                        logger.warning(f"[OnboardAI][LIFECYCLE] digital_only branch lookup failed: {_br_err}")
                    
                    _complete_data = {
                        "account_number": _acct_no,
                        "account_type": getattr(user, "account_type", "") if user else "",
                        "name": getattr(user, "name", "") if user else "",
                        "father_name": getattr(user, "father_name", "") if user else "",
                        **_branch_data,
                    }
                    
                    if user and user.email:
                        try:
                            from app.services.email_service import send_approval_email
                            asyncio.create_task(send_approval_email(
                                email=user.email,
                                name=user.name or "",
                                application_id=session_ulid,
                                account_number=_acct_no,
                                branch_name=_branch_data.get("branch_name", ""),
                                branch_address=_branch_data.get("branch_address", ""),
                                ifsc=_branch_data.get("pref_branch_ifsc", "CDBB0008863"),
                                account_type=user.account_type or "",
                                manager_name=_branch_data.get("manager_name"),
                                relationship_officer=_branch_data.get("relationship_officer"),
                            ))
                            logger.info(f"[OnboardAI][EMAIL] Lifecycle digital approval email task queued for {session_ulid}")
                        except Exception as _email_err:
                            logger.warning(f"[OnboardAI][EMAIL] Non-fatal: digital approval email task failed: {_email_err}")

                    return {
                        "ui_action": "RENDER_AUTO_APPROVE",
                        "session_ulid": session_ulid,
                        "data_required": [],
                        "agent_message": (
                            f"Your {_lc_label} is complete. "
                            "Your digital account has been updated and is under review."
                        ),
                        "extracted_data": _complete_data,
                    }
                else:
                    # ── Task 4: Retail/SME — Continue to Additional Info ──────
                    # Do NOT finalize yet. Do NOT clear lifecycle flags.
                    # The flow is only complete after additional_info is submitted.
                    _is_sme = _acct_type == "sme_current"
                    from app.services.additional_info_service import get_form_schema
                    _schema = get_form_schema(_acct_type)
                    logger.info(f"[OnboardAI][LIFECYCLE] {_acct_type} face verified. Routing to RENDER_ADDITIONAL_INFO.")
                    return {
                        "ui_action": "RENDER_ADDITIONAL_INFO",
                        "session_ulid": session_ulid,
                        "data_required": _schema,
                        "agent_message": (
                            f"Face verification successful! As part of your {_lc_label}, "
                            f"please update your {'business' if _is_sme else 'personal'} profile information below."
                        ),
                        "extracted_data": {
                            "account_type": _acct_type,
                            "lifecycle_intent": _lc_flag,
                            "form_schema": _schema
                        }
                    }

            # ── Standard Onboarding: Digital-Only fast-path ──────────────────
            if user.account_type == "digital_only":
                # ── Phase 4 Hook: Risk evaluation BEFORE final success (Digital-Only) ──
                _face_raw = await redis_client.get(f"face_verification:{session_ulid}")
                _face_res = json.loads(_face_raw) if _face_raw else {}
                _risk_routing = await _apply_risk_routing(
                    user=user, db=db, session_ulid=session_ulid,
                    face_result=_face_res, current_state=current_state
                )
                if _risk_routing["_risk_action"] != "PROCEED":
                    return _risk_routing

                from app.db.models.user import AdditionalInfo
                from app.db.models.bank_branch import BankBranch
                import uuid
                import random
                
                def _generate_account_number() -> str:
                    return str(random.randint(1_000_000_000, 9_999_999_999))
                    
                _acct_no = _generate_account_number()
                
                # Upsert AdditionalInfo for digital with default branch and generated account number
                _stmt_info = select(AdditionalInfo).where(AdditionalInfo.session_ulid == session_ulid)
                _res_info = await db.execute(_stmt_info)
                _existing_info = _res_info.scalar_one_or_none()
                
                if _existing_info:
                    _existing_info.pref_branch = "CDBB0008863"
                    _existing_info.account_number = _acct_no
                else:
                    _new_info = AdditionalInfo(
                        id=str(uuid.uuid4()),
                        session_ulid=session_ulid,
                        data={},
                        pref_branch="CDBB0008863",
                        account_number=_acct_no
                    )
                    db.add(_new_info)
                
                await db.commit()
                
                # Fetch branch details for email payload
                _branch_data: dict = {}
                try:
                    _br_res = await db.execute(select(BankBranch).where(BankBranch.ifsc == "CDBB0008863"))
                    _branch = _br_res.scalars().first()
                    if _branch:
                        _branch_data = {
                            "pref_branch_ifsc": _branch.ifsc,
                            "branch_name": _branch.branch_name,
                            "branch_address": _branch.branch_address,
                            "manager_name": _branch.manager_name,
                            "relationship_officer": _branch.relationship_officer,
                        }
                except Exception as _br_err:
                    logger.warning(f"[OnboardAI][FAST_PATH] digital_only branch lookup failed: {_br_err}")
                
                _complete_data = {
                    "account_number": _acct_no,
                    "account_type": getattr(user, "account_type", "") if user else "",
                    "name": getattr(user, "name", "") if user else "",
                    "father_name": getattr(user, "father_name", "") if user else "",
                    **_branch_data,
                }
                
                if user and user.email:
                    try:
                        from app.services.email_service import send_approval_email
                        asyncio.create_task(send_approval_email(
                            email=user.email,
                            name=user.name or "",
                            application_id=session_ulid,
                            account_number=_acct_no,
                            branch_name=_branch_data.get("branch_name", ""),
                            branch_address=_branch_data.get("branch_address", ""),
                            ifsc=_branch_data.get("pref_branch_ifsc", "CDBB0008863"),
                            account_type=user.account_type or "",
                            manager_name=_branch_data.get("manager_name"),
                            relationship_officer=_branch_data.get("relationship_officer"),
                        ))
                        logger.info(f"[OnboardAI][EMAIL] Digital approval email task queued for {session_ulid}")
                    except Exception as _email_err:
                        logger.warning(f"[OnboardAI][EMAIL] Non-fatal: digital approval email task failed: {_email_err}")

                return {
                    "ui_action": "RENDER_AUTO_APPROVE",
                    "session_ulid": session_ulid,
                    "data_required": [],
                    "agent_message": "Identity verified! Your digital account has been successfully opened.",
                    "extracted_data": _complete_data,
                }
        
    # ── FAST PATH: OTP / Auth Handlers ────────────────────────────────────────
    # These handlers are deterministic and need NO Gemini LLM, NO resolved_state,
    # and NO get_dynamic_state.  They were previously located INSIDE the SLOW PATH
    # block, which meant every OTP send/verify triggered a full DB state load + the
    # "SLOW PATH" log line, adding 1–3 seconds of unnecessary latency.
    #
    # Handlers that need user_obj do a lightweight lazy-load here instead of
    # relying on the SLOW PATH's selectinload query.

    # ── FIX: TRIGGER_OTP_SEND Disambiguation ──────────────────────────────────
    # The frontend sends "SYSTEM: TRIGGER_OTP_SEND" for BOTH phone and email
    # stages.  Without this guard, after phone verification the fast-path still
    # maps it to phone_send_otp, causing a duplicate phone OTP.  We check
    # pending_auth in Redis: if the phone was already verified (key deleted or
    # phone_verified flag set), we redirect to email_send_otp.
    if source == "phone_send_otp" and session_ulid:
        _pending_raw = await redis_client.get(f"pending_auth:{session_ulid}")
        if _pending_raw:
            _pending_data = json.loads(_pending_raw)
            if _pending_data.get("phone_verified"):
                logger.info(f"[OnboardAI][FAST_PATH] TRIGGER_OTP_SEND disambiguation: phone already verified → email_send_otp")
                source = "email_send_otp"
        else:
            # pending_auth key missing but phone might be verified in DB
            _phone_check = await db.execute(
                select(UserInitial.phone).where(UserInitial.id == session_ulid)
            )
            _phone_row = _phone_check.scalar_one_or_none()
            if _phone_row is not None:
                logger.info(f"[OnboardAI][FAST_PATH] TRIGGER_OTP_SEND disambiguation (DB): phone already verified → email_send_otp")
                source = "email_send_otp"

    # ── Handler: phone_send_otp ───────────────────────────────────────────────
    if source == "phone_send_otp" or (not source and "SYSTEM: TRIGGER_OTP_SEND" in message):
        phone_no = current_state.get("phone", current_state.get("contact", ""))
        if phone_no:
            logger.info(f"[OnboardAI][FAST PATH] Triggering phone OTP for {phone_no}")
            try:
                res = await send_phone_otp(phone_no, pending_session_id=session_ulid)
                return {
                    "ui_action": "RENDER_PHONE_AUTH",
                    "session_ulid": res["session_ulid"],
                    "data_required": ["phone", "otp"],
                    "agent_message": f"OTP successfully dispatched to {phone_no}."
                }
            except Exception as e:
                error_msg = getattr(e, "detail", str(e))
                return {
                    "ui_action": "RENDER_PHONE_AUTH",
                    "session_ulid": session_ulid,
                    "data_required": ["phone"],
                    "agent_message": f"Failed to send OTP: {error_msg}"
                }

    # ── Handler: phone_verify_otp ─────────────────────────────────────────────
    if source == "phone_verify_otp":
        raw_code = current_state.get("otp") or current_state.get("code") or message
        code = "".join(filter(str.isdigit, str(raw_code)))
        logger.info(f"[OnboardAI][FAST PATH] Verifying phone OTP {code} for session {session_ulid}")
        # Lazy-load user_obj for lifecycle logic (lightweight — no selectinload)
        _pv_result = await db.execute(select(UserInitial).where(UserInitial.id == session_ulid))
        _pv_user_obj = _pv_result.scalar_one_or_none()
        if session_ulid:
            pending_raw = await redis_client.get(f"pending_auth:{session_ulid}")
            if pending_raw:
                phone_no = json.loads(pending_raw).get("phone")
                try:
                    res = await verify_phone_otp(phone_no, code, pending_session_id=session_ulid)
                    # ── Lifecycle Logic: Auto-trigger email and enhance payload ──
                    from app.agents.lifecycle_agent import LifecycleOrchestrator
                    _lc_intent = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid)
                    
                    _data_req = ["email"]
                    _extra = {}
                    _msg = "Phone verified successfully. Please enter your email address to continue."
                    
                    if _lc_intent and _pv_user_obj:
                         try:
                             await send_email_otp(_pv_user_obj.email, pending_session_id=session_ulid)
                             logger.info(f"[OnboardAI][LIFECYCLE] Auto-triggered email OTP for {_pv_user_obj.email}")
                             _data_req = ["otp"]
                             _extra = {
                                 "contact": _pv_user_obj.email,
                                 "masked_contact": _pv_user_obj.email,
                                 "is_otp_sent": True,
                                 "otp_status": "sent",
                                 "otp_expiry": 180,
                                 "lifecycle_intent": _lc_intent
                             }
                             _msg = f"Phone verified! We've automatically sent a code to your registered email: {_pv_user_obj.email}. Please enter it below."
                         except Exception as e:
                             logger.info(f"[OnboardAI][LIFECYCLE] Failed to auto-trigger email: {e}")

                    return {
                        "ui_action": "RENDER_EMAIL_AUTH",
                        "session_ulid": res["pending_session_id"],
                        "data_required": _data_req,
                        "agent_message": _msg,
                        "extracted_data": _extra,
                        "current_state": {
                             "phone": _pv_user_obj.phone if _pv_user_obj else None,
                             "email": _pv_user_obj.email if _pv_user_obj else None,
                             "contact": _pv_user_obj.email if _pv_user_obj else None
                        }
                    }
                except Exception as e:
                    error_msg = getattr(e, "detail", str(e))
                    # ── Lifecycle Logic: Failure payload ──
                    from app.agents.lifecycle_agent import LifecycleOrchestrator
                    _lc_intent = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid)
                    _data_req = ["phone", "otp"]
                    _extra = {}
                    if _lc_intent and _pv_user_obj:
                         _data_req = ["otp"]
                         from app.agents.lifecycle_agent import mask_phone
                         _extra = {
                             "contact": _pv_user_obj.phone,
                             "masked_contact": mask_phone(_pv_user_obj.phone),
                             "is_otp_sent": True,
                             "otp_status": "sent",
                             "otp_expiry": 180,
                             "lifecycle_intent": _lc_intent
                         }

                    return {
                        "ui_action": "RENDER_PHONE_AUTH",
                        "session_ulid": session_ulid,
                        "data_required": _data_req,
                        "agent_message": f"Verification failed: {error_msg}",
                        "extracted_data": _extra,
                        "current_state": {
                             "phone": _pv_user_obj.phone if _pv_user_obj else None,
                             "contact": _pv_user_obj.phone if _pv_user_obj else None
                        }
                    }

    # ── Handler: email_send_otp ───────────────────────────────────────────────
    if source == "email_send_otp" or "SYSTEM: TRIGGER_EMAIL_OTP" in message:
        email_addr = current_state.get("email", "")
        if email_addr:
            logger.info(f"[OnboardAI][FAST PATH] Triggering email OTP for {email_addr}")
            try:
                await send_email_otp(email_addr, pending_session_id=session_ulid)
                # ── Lifecycle Logic: Enhance payload ──
                from app.agents.lifecycle_agent import LifecycleOrchestrator
                _lc_intent = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid)
                
                _data_req = ["email", "otp"]
                _extra = {}
                if _lc_intent:
                    _data_req = ["otp"]
                    _extra = {
                        "contact": email_addr,
                        "masked_contact": email_addr,
                        "is_otp_sent": True,
                        "otp_status": "sent",
                        "otp_expiry": 180,
                        "lifecycle_intent": _lc_intent
                    }

                return {
                    "ui_action": "RENDER_EMAIL_AUTH",
                    "session_ulid": session_ulid,
                    "data_required": _data_req,
                    "agent_message": f"OTP successfully dispatched to {email_addr}.",
                    "extracted_data": _extra,
                    "current_state": {
                        "email": email_addr,
                        "contact": email_addr
                    }
                }
            except Exception as e:
                error_msg = getattr(e, "detail", str(e))
                return {
                    "ui_action": "RENDER_EMAIL_AUTH",
                    "session_ulid": session_ulid,
                    "data_required": ["email"],
                    "agent_message": f"Failed to send OTP: {error_msg}"
                }

    # ── Handler: email_verify_otp ─────────────────────────────────────────────
    if source == "email_verify_otp":
        raw_code = current_state.get("otp") or current_state.get("code") or message
        code = "".join(filter(str.isdigit, str(raw_code)))
        logger.info(f"[OnboardAI][FAST PATH] Verifying email OTP {code} for session {session_ulid}")
        # Lazy-load user_obj for lifecycle error handler
        _ev_result = await db.execute(select(UserInitial).where(UserInitial.id == session_ulid))
        _ev_user_obj = _ev_result.scalar_one_or_none()
        try:
            res = await verify_email_otp(code, pending_session_id=session_ulid)
            await redis_client.delete(f"pending_auth:{session_ulid}")

            # ── Task 3: Lifecycle Email Bypass ──────────────────────────────────
            # Check if this is a lifecycle flow. If so, skip register_user (no INSERT)
            # and use upsert_user_data to update the existing record instead.
            from app.agents.lifecycle_agent import LifecycleOrchestrator
            lifecycle_intent = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid)

            if lifecycle_intent:
                logger.info(f"[OnboardAI][LIFECYCLE] Email verified for lifecycle flow '{lifecycle_intent}'. Updating record.")
                await LifecycleOrchestrator.upsert_user_data(
                    session_ulid,
                    {"email": res.get("email")},  # Refresh email if changed, Data Guard filters None
                    db
                )
                
                # ── BUG FIX: Mirror session to Redis for Middleware Auth ──
                # Because lifecycle bypasses register_user(), we must manually cache the
                # session token in Redis so the /upload-documents middleware auth succeeds.
                if _ev_user_obj:
                    try:
                        session_data = json.dumps({
                            "uid": _ev_user_obj.id,
                            "phone_number": _ev_user_obj.phone,
                            "email": res.get("email") or _ev_user_obj.email,
                        })
                        await redis_client.setex(f"session:{session_ulid}", 30 * 60, session_data)
                        logger.info(f"[OnboardAI][LIFECYCLE] Cached session:{session_ulid} in Redis for document upload auth")
                    except Exception as e:
                        logger.warning(f"[OnboardAI][LIFECYCLE] Failed to cache session in Redis: {e}")

                # ── Task 1: Use anchored account_type (not re-queried from DB) ──
                # Redis key was set during lifecycle_init so we know the original type.
                anchored_raw = await redis_client.get(f"lifecycle_account_type:{session_ulid}")
                if anchored_raw:
                    acct = anchored_raw.decode('utf-8') if isinstance(anchored_raw, bytes) else anchored_raw
                else:
                    # Fallback: query db if anchor missing
                    result = await db.execute(select(UserInitial).where(UserInitial.id == session_ulid))
                    existing_user = result.scalar_one_or_none()
                    acct = existing_user.account_type if existing_user else "retail_savings"
                logger.info(f"[OnboardAI][LIFECYCLE] Anchored account_type='{acct}' for {session_ulid}")
                required_docs = ["aadhaar", "pan"] + (["gst"] if acct == "sme_current" else [])
                return {
                    "ui_action": "RENDER_KYC_UPLOAD",
                    "session_ulid": session_ulid,
                    "data_required": required_docs,
                    "agent_message": (
                        f"Identity confirmed! Please re-upload your KYC documents "
                        f"to complete your {lifecycle_intent.replace('_', ' ').upper()} request."
                    ),
                    "extracted_data": {"account_type": acct, "lifecycle_intent": lifecycle_intent}
                }

            # ── Standard Onboarding: Register new user ──────────────────────────
            from app.agents.entry_agent import register_user
            user = await register_user(db, phone=res["phone"], email=res["email"], session_ulid=session_ulid)

            return {
                "ui_action": "RENDER_CHAT",
                "session_ulid": user.id,
                "data_required": ["intent"],
                "agent_message": "Email verified! To proceed, please select the type of account you wish to open: Retail Savings, Digital-Only, or SME Current."
            }
        except Exception as e:
            logger.exception(f"[OnboardAI][ERROR] verification exception: {repr(e)}")
            error_msg = f"{repr(e)} | Detail: {getattr(e, 'detail', str(e))}"
            # ── Lifecycle Logic: Failure payload ──
            from app.agents.lifecycle_agent import LifecycleOrchestrator
            _lc_intent = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid)
            _data_req = ["email", "otp"]
            _extra = {}
            if _lc_intent and _ev_user_obj:
                 _data_req = ["otp"]
                 _extra = {
                     "contact": _ev_user_obj.email,
                     "masked_contact": _ev_user_obj.email,
                     "is_otp_sent": True,
                     "otp_status": "sent",
                     "otp_expiry": 180,
                     "lifecycle_intent": _lc_intent
                 }

            return {
                "ui_action": "RENDER_EMAIL_AUTH",
                "session_ulid": session_ulid,
                "data_required": _data_req,
                "agent_message": f"Verification failed: {error_msg}",
                "extracted_data": _extra,
                "current_state": {
                    "email": _ev_user_obj.email if _ev_user_obj else None,
                    "contact": _ev_user_obj.email if _ev_user_obj else None
                }
            }

    # ── FAST PATH: Lifecycle ID Validation (Requirement 1) ───────────────────────
    _awaiting_id_key = f"lifecycle_awaiting_id:{session_ulid}"
    if session_ulid and await redis_client.exists(_awaiting_id_key):
        from app.agents.lifecycle_agent import LifecycleOrchestrator
        account_id = message.strip()
        logger.info(f"[OnboardAI][LIFECYCLE] Validating ID: {account_id}")
        
        # Get intent from transient Redis state
        intent_raw = await redis_client.get(_awaiting_id_key)
        _lc_intent = intent_raw.decode('utf-8') if isinstance(intent_raw, bytes) else intent_raw
        
        try:
            lc = LifecycleOrchestrator(intent=_lc_intent)
            user = await lc.lookup_account(account_id, db)
            
            if user:
                # 1. Hijack session — use account_id as the session_ulid
                old_session_ulid = session_ulid
                session_ulid = user.id
                
                # 2. Inject phone/email into pending_auth (Correction 1)
                try:
                    await send_phone_otp(user.phone, pending_session_id=session_ulid)
                    logger.info(f"[OnboardAI][LIFECYCLE] OTP triggered for {user.phone} for session {session_ulid}")
                except Exception as otp_err:
                    logger.info(f"[OnboardAI][LIFECYCLE] Failed to trigger initial OTP: {otp_err}")
                
                # 3. Anchor account_type and set lifecycle flags
                await redis_client.setex(f"lifecycle_account_type:{session_ulid}", 86400, user.account_type or "retail_savings")
                await LifecycleOrchestrator.set_lifecycle_flag(session_ulid, _lc_intent)
                
                # 4. Mark session status (Requirement 4)
                await LifecycleOrchestrator.upsert_user_data(
                    session_ulid,
                    {"status": "RE_KYC_PENDING" if _lc_intent == 're_kyc' else "REACTIVATION_PENDING"},
                    db
                )
                
                # Cleanup transient state
                await redis_client.delete(_awaiting_id_key)
                
                logger.info(f"[OnboardAI][LIFECYCLE] ID FOUND. Injected phone/email for {session_ulid}. Transitioning to OTP.")
                return lc.get_initial_action(user)
            else:
                return {
                    "ui_action": "RENDER_CHAT",
                    "session_ulid": session_ulid,
                    "data_required": [],
                    "agent_message": f"Account ID '{account_id}' not found. Please try again or type 'New Account'."
                }
        except Exception as e:
            logger.error(f"[OnboardAI][LIFECYCLE] ID validation error: {e}")
            return {
                "ui_action": "RENDER_ERROR",
                "agent_message": "An error occurred during account validation. Please try again."
            }

    # ── SLOW PATH: Standard chat — goes through Gemini LLM ────────────────────
    logger.info(f"[OnboardAI][ROUTER] 🔄 SLOW PATH: Routing to Gemini LLM orchestrator")
    
    # Ensure source is populated if provided nested in current_state
    if not source and isinstance(current_state, dict):
        source = current_state.get("source")

    # 1. Resolve State
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(UserInitial)
        .where(UserInitial.id == session_ulid)
        .options(selectinload(UserInitial.additional_info))
    )
    user_obj = result.scalar_one_or_none()
    
    # Wait, the New Session Bootstrap was removed because the Chronological Evaluator
    # now handles Phone/Email gates directly for unauthenticated users.
    
    resolved_state = await get_dynamic_state(session_ulid, db)

    # ── FAST PATH: Explicit Account Selection ─────────────────────────────────
    # This phase handles the explicit choice after Email Auth or whenever the system asks.
    if source == "account_selection":
        from app.agents.intent_agent import classify_intent, LIFECYCLE_INTENTS, IntentCategory
        from app.agents.lifecycle_agent import LifecycleOrchestrator

        # ── Layer 2: Semantic Cache (before LLM) ──────────────────────────────
        from app.agents.intent_cache_service import check_semantic_cache, populate_cache_async
        _cached_intent = await check_semantic_cache(message)
        if _cached_intent:
            logger.info(f"[OnboardAI][INTENT] ⚡ CACHE HIT: '{message[:60]}' → '{_cached_intent}' (skipping LLM)")
            try:
                _detected_intent = IntentCategory(_cached_intent)
            except ValueError:
                _detected_intent = IntentCategory.UNKNOWN
            classification = type('CacheResult', (), {'intent': _detected_intent, 'reasoning': 'semantic cache hit'})()
        else:
            # ── Slow Path: LLM Classification ─────────────────────────────────
            logger.info(f"[OnboardAI][INTENT] 🔄 SLOW PATH: Calling LLM for intent classification: {message[:100]}...")
            classification = await classify_intent(message)
            _detected_intent = classification.intent
            # Fire-and-forget: populate cache for future hits
            from app.agents.intent_cache_service import CACHEABLE_INTENTS
            if _detected_intent != IntentCategory.UNKNOWN and _detected_intent.value in CACHEABLE_INTENTS:
                asyncio.create_task(populate_cache_async(message, _detected_intent.value))

        # ── Lifecycle Detected during Account Selection ───────────────────────
        if _detected_intent in LIFECYCLE_INTENTS and session_ulid:
            label = "Re-KYC" if _detected_intent == IntentCategory.RE_KYC else "Account Reactivation"
            logger.info(f"[OnboardAI][LIFECYCLE] Detected '{_detected_intent}' during account selection.")
            # Transition session to lifecycle flow
            await LifecycleOrchestrator.set_lifecycle_flag(session_ulid, _detected_intent.value)
            return {
                "ui_action": "RENDER_CHAT",
                "session_ulid": session_ulid,
                "data_required": ["account_id"],
                "agent_message": (
                    f"I understand you'd like to start a {label} process. "
                    f"Please provide your Account ID (the unique identifier linked to your existing account) to continue."
                ),
                "extracted_data": {"lifecycle_intent": _detected_intent.value}
            }

        # ── Task 3: Intent Validation Guard for Existing Lifecycle Flows ───────────
        _lc_intent = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid) if session_ulid else None
        if _lc_intent:
            anchored_raw = await redis_client.get(f"lifecycle_account_type:{session_ulid}")
            _anchored_type = (anchored_raw.decode('utf-8') if isinstance(anchored_raw, bytes) else anchored_raw) if anchored_raw else None
            if _anchored_type and str(_detected_intent) != _anchored_type and str(_detected_intent) not in ("unknown", _anchored_type):
                logger.info(f"[OnboardAI][LIFECYCLE] Mismatch detected! User={_detected_intent}, DB={_anchored_type}. Forcing original.")
                _display = _anchored_type.replace('_', ' ').title()
                _detected_intent = _anchored_type  # Override with DB truth
                # Persist the correction
                docs = ["aadhaar", "pan"] + (["gst"] if _detected_intent == "sme_current" else [])
                return {
                    "ui_action": "RENDER_KYC_UPLOAD",
                    "session_ulid": session_ulid,
                    "data_required": docs,
                    "agent_message": (
                        f"This account is registered as a {_display} account. "
                        f"We will proceed with the Re-KYC for your {_display} profile. "
                        f"Please upload your documents to continue."
                    ),
                    "extracted_data": {"account_type": _detected_intent}
                }
        
        if _detected_intent and _detected_intent != "unknown" and session_ulid:
            _intent_stmt = select(UserInitial).where(UserInitial.id == session_ulid)
            _intent_res  = await db.execute(_intent_stmt)
            _intent_user = _intent_res.scalar_one_or_none()
            if _intent_user:
                logger.info(f"[OnboardAI][INTENT] LLM Confirmed: '{_detected_intent}' (reason: {classification.reasoning})")
                _intent_str = _detected_intent.value if hasattr(_detected_intent, "value") else str(_detected_intent)
                
                try:
                    _intent_user.account_type = _intent_str
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    logger.error(f"[OnboardAI][INTENT] DB Commit failed (Duplicate account): {e}")
                    
                    # --- Auto-Resolve Draft Conflict ---
                    _existing_q = select(UserInitial).where(
                        (UserInitial.phone == _intent_user.phone) &
                        (UserInitial.account_type == _intent_str)
                    )
                    existing_res = await db.execute(_existing_q)
                    existing_draft = existing_res.scalar_one_or_none()
                    if not existing_draft and _intent_user.email:
                        _existing_q2 = select(UserInitial).where(
                            (UserInitial.email == _intent_user.email) &
                            (UserInitial.account_type == _intent_str)
                        )
                        existing_res = await db.execute(_existing_q2)
                        existing_draft = existing_res.scalar_one_or_none()

                    if existing_draft and existing_draft.status == 'draft' and existing_draft.id != session_ulid:
                        logger.info(f"[OnboardAI][INTENT] Deleting conflicting draft {existing_draft.id} to allow new session {session_ulid}")
                        await db.delete(existing_draft)
                        await db.commit()
                        try:
                            _intent_user.account_type = _intent_str
                            await db.commit()
                        except Exception as retry_e:
                            await db.rollback()
                            return {
                                "ui_action": "RENDER_CHAT",
                                "session_ulid": session_ulid,
                                "data_required": ["intent"],
                                "agent_message": f"You already have an existing {_intent_str.replace('_', ' ').title()} account associated with this email or phone number. You cannot open a duplicate account of the same type."
                            }
                    else:
                        return {
                            "ui_action": "RENDER_CHAT",
                            "session_ulid": session_ulid,
                            "data_required": ["intent"],
                            "agent_message": f"You already have an existing {_intent_str.replace('_', ' ').title()} account associated with this email or phone number. You cannot open a duplicate account of the same type."
                        }
                
                # Determine dynamic documents
                docs = ["aadhaar", "pan"]
                if _intent_str == "sme_current":
                    docs.append("gst")
                
                msg = f"Great! I've confirmed your interest in a {_intent_str.replace('_', ' ').title()} account. Please upload your documents to proceed."
                return {
                    "ui_action": "RENDER_KYC_UPLOAD",
                    "session_ulid": session_ulid,
                    "data_required": docs,
                    "agent_message": msg,
                    "extracted_data": {"account_type": _intent_str}
                }
        else:
            logger.info(f"[OnboardAI][INTENT] LLM returned 'unknown' or session missing. Re-prompting.")
            return {
                "ui_action": "RENDER_CHAT",
                "session_ulid": session_ulid,
                "data_required": ["intent"],
                "agent_message": "I couldn't quite catch the account type you're looking for. Could you please specify if you want a Retail Savings, Digital-Only, SME Current, Re-KYC, or Account Reactivation?"
            }

    # ── FAST PATH: Deterministic Intent Mapping (Auth Incomplete) ─────────────
    # Evaluated to cache intent BEFORE auth is done.
    _intent_from_chat = None
    if not source or source == "chat_send":
        from app.agents.intent_agent import classify_intent, IntentCategory, LIFECYCLE_INTENTS
        from app.agents.intent_cache_service import check_semantic_cache, populate_cache_async

        # ── Layer 2: Semantic Cache (before LLM) ──────────────────────────────
        _cached_intent = await check_semantic_cache(message)
        if _cached_intent:
            logger.info(f"[OnboardAI][INTENT] ⚡ CACHE HIT (pre-auth): '{message[:60]}' → '{_cached_intent}'")
            try:
                _cached_cat = IntentCategory(_cached_intent)
            except ValueError:
                _cached_cat = IntentCategory.UNKNOWN
            classification = type('CacheResult', (), {'intent': _cached_cat, 'reasoning': 'semantic cache hit'})()
        else:
            # ── Slow Path: LLM Classification ─────────────────────────────────
            classification = await classify_intent(message)
            logger.info(f"[OnboardAI][INTENT] 🔄 SLOW PATH pre-auth result: {classification.intent} for: {message[:60]!r}")
            # Fire-and-forget: populate cache for future hits (including UNKNOWN)
            from app.agents.intent_cache_service import CACHEABLE_INTENTS
            if classification.intent.value in CACHEABLE_INTENTS:
                asyncio.create_task(populate_cache_async(message, classification.intent.value))

        # ── DISAMBIGUATION TRAP: Intercept UNKNOWN before state evaluator ─────
        # If intent is UNKNOWN (greeting, chit-chat, ambiguous), return a
        # clarification prompt IMMEDIATELY — BUT ONLY if this is a truly new
        # session with no account_type. If the user already has a saved
        # account_type, they are in an active flow and a generic greeting
        # should NOT wipe their progress.
        if classification.intent == IntentCategory.UNKNOWN:
            # ── Context-Aware Check: Does the DB/Redis already have an account_type? ──
            _existing_account_type = None
            if user_obj and getattr(user_obj, "account_type", None):
                _existing_account_type = user_obj.account_type
            else:
                # Fallback: check Redis session_intent cache
                _intent_raw = await redis_client.get(f"session_intent:{session_ulid}")
                if _intent_raw:
                    _existing_account_type = _intent_raw.decode("utf-8") if isinstance(_intent_raw, bytes) else str(_intent_raw)

            if _existing_account_type:
                # User is in an active flow — ignore the generic greeting
                # and let the state evaluator continue from where they left off.
                logger.info(
                    f"[OnboardAI][INTENT] ✅ UNKNOWN TRAP BYPASSED: session {session_ulid} "
                    f"already has account_type='{_existing_account_type}'. Ignoring '{message[:60]}'."
                )
                _intent_from_chat = _existing_account_type
                await redis_client.setex(f"session_intent:{session_ulid}", 86400, str(_existing_account_type))
            else:
                # True new session — no account_type anywhere. Fire the disambiguation.
                logger.info(f"[OnboardAI][INTENT] 🛑 UNKNOWN TRAP: '{message[:60]}' — returning disambiguation prompt")
                return {
                    "ui_action": "RENDER_CHAT",
                    "session_ulid": session_ulid,
                    "data_required": ["intent"],
                    "agent_message": (
                        "Hello! To get started, please tell me which type of account "
                        "you would like to open (e.g., Retail Savings, SME Current, "
                        "Digital-Only, Re-KYC, or Account Reactivation)."
                    )
                }

        if classification.intent in LIFECYCLE_INTENTS and session_ulid:
            # ── Lifecycle Detected Pre-Auth: Prompt for Account ID (Requirement 1) ──
            label = "Re-KYC" if classification.intent == IntentCategory.RE_KYC else "Account Reactivation"
            
            # Set transient flag to expect ID in next turn
            await redis_client.setex(f"lifecycle_awaiting_id:{session_ulid}", 600, classification.intent.value)
            
            return {
                "ui_action": "RENDER_CHAT",
                "session_ulid": session_ulid,
                "data_required": [], # We want the raw chat message for the ID
                "agent_message": (
                    f"I understand you'd like to start a {label} process. "
                    "Please provide your Account Number (ID) to continue."
                ),
                "extracted_data": {"lifecycle_intent": classification.intent.value}
            }

        elif classification.intent != IntentCategory.UNKNOWN and session_ulid:
            _intent_from_chat = classification.intent.value
            await redis_client.setex(f"session_intent:{session_ulid}", 86400, str(_intent_from_chat))
            logger.info(f"[OnboardAI][INTENT] Detected and Cached Intent='{_intent_from_chat}' for {session_ulid}")

    # ── STRICT CHRONOLOGICAL STATE EVALUATOR ──────────────────────────────────
    # Checks prerequisites in strict sequential order. No step fires until all
    # prior steps are confirmed complete. Runs AFTER _INTENT_MAP above.

    # ── Task 6 + Task 2: Lifecycle State Reset ───────────────────────────
    # Force-override resolved_state for lifecycle flows:
    # - phoneVerified / emailVerified: keep real values (user must re-auth)
    # - faceVerified: ALWAYS False (user must redo face scan)
    # - kycUploaded: ALWAYS False (user must re-upload docs)
    # This prevents the 'already completed' skip-bug.
    from app.agents.lifecycle_agent import LifecycleOrchestrator
    _lifecycle_intent = await LifecycleOrchestrator.get_lifecycle_flag(session_ulid) if session_ulid else None
    if _lifecycle_intent:
        logger.info(f"[OnboardAI][LIFECYCLE] Requirement 3 state reset: forcing KYC/face flags to False for '{_lifecycle_intent}'")
        
        # Determine if we need additional info (Requirement 4)
        anchored_raw = await redis_client.get(f"lifecycle_account_type:{session_ulid}")
        _anchored_type = (anchored_raw.decode('utf-8') if isinstance(anchored_raw, bytes) else anchored_raw) if anchored_raw else None
        
        _needs_add_info = _anchored_type in ("retail_savings", "sme_current")
        
        resolved_state = {
            "phoneVerified": resolved_state.get("phoneVerified", False),
            "emailVerified": resolved_state.get("emailVerified", False),
            # Requirement 3: Force these to False
            "faceVerified": False,
            "kycUploaded":  False,
            "additionalInfoSubmitted": not _needs_add_info, # If not needed, treat as submitted
            "_lifecycle_intent": _lifecycle_intent,
            "account_type": _anchored_type
        }

    # Step 1 — Phone verification
    if not resolved_state.get("phoneVerified"):
        logger.info(f"[OnboardAI][STATE] Step 1 incomplete: phone unverified")
        return {
            "ui_action": "RENDER_PHONE_AUTH",
            "session_ulid": session_ulid,
            "data_required": ["phone"],
            "agent_message": "Hello! To get started with your account opening, please provide your phone number for verification.",
        }

    # Step 2 — Email verification
    if not resolved_state.get("emailVerified"):
        logger.info(f"[OnboardAI][STATE] Step 2 incomplete: email unverified")
        
        # ── Lifecycle Logic: Initialization Payload ──
        extracted_data = {}
        data_req = ["email"]
        if _lifecycle_intent:
            data_req = ["otp"] # Verification Mode only
            extracted_data = {
                "contact": user_obj.email,
                "masked_contact": user_obj.email, # Or mask if needed
                "is_otp_sent": True,
                "otp_status": "sent",
                "otp_expiry": 180,
                "lifecycle_intent": _lifecycle_intent
            }
        
        return {
            "ui_action": "RENDER_EMAIL_AUTH",
            "session_ulid": session_ulid,
            "data_required": data_req,
            "agent_message": "Please verify your email address to continue.",
            "extracted_data": extracted_data,
            "current_state": {
                "email": user_obj.email if user_obj else None,
                "contact": user_obj.email if user_obj else None
            }
        }

    if user_obj:
        _acct       = user_obj.account_type or ""
        _is_sme_ses = _acct == "sme_current"

        # Step 3 — Account type / intent
        if not _acct:
            logger.info(f"[OnboardAI][INTENT_DEBUG] Step 3: No account_type in DB. Checking Cache/Chat...")
            
            # Use intent from current chat processing if available
            _acct = _intent_from_chat
            
            # If not in current chat, check Redis cache
            if not _acct:
                cached_intent_raw = await redis_client.get(f"session_intent:{session_ulid}")
                if cached_intent_raw:
                    _acct = cached_intent_raw.decode('utf-8') if isinstance(cached_intent_raw, bytes) else cached_intent_raw
                    logger.info(f"[OnboardAI][STATE] Promoting cached intent '{_acct}' to DB for {session_ulid}")

            # ── Requirement 2: Automated Intent Bypass ───────────────────────
            if _lifecycle_intent and _anchored_type:
                logger.info(f"[OnboardAI][LIFECYCLE] Requirement 2: Bypassing Step 3 with anchored type '{_anchored_type}'")
                _acct = _anchored_type
            
            if _acct:
                try:
                    user_obj.account_type = _acct
                    await db.commit()
                    # Clear cache after promotion
                    await redis_client.delete(f"session_intent:{session_ulid}")
                except Exception as e:
                    await db.rollback()
                    logger.error(f"[OnboardAI][STATE] DB Commit failed (Duplicate account): {e}")
                    
                    _existing_q = select(UserInitial).where(
                        (UserInitial.phone == user_obj.phone) &
                        (UserInitial.account_type == _acct)
                    )
                    existing_res = await db.execute(_existing_q)
                    existing_draft = existing_res.scalar_one_or_none()
                    if not existing_draft and user_obj.email:
                        _existing_q2 = select(UserInitial).where(
                            (UserInitial.email == user_obj.email) &
                            (UserInitial.account_type == _acct)
                        )
                        existing_res = await db.execute(_existing_q2)
                        existing_draft = existing_res.scalar_one_or_none()

                    if existing_draft and existing_draft.status == 'draft' and existing_draft.id != session_ulid:
                        logger.info(f"[OnboardAI][STATE] Deleting conflicting draft {existing_draft.id} to allow new session {session_ulid}")
                        await db.delete(existing_draft)
                        await db.commit()
                        try:
                            user_obj.account_type = _acct
                            await db.commit()
                        except Exception as retry_e:
                            await db.rollback()
                            return {
                                "ui_action": "RENDER_CHAT",
                                "session_ulid": session_ulid,
                                "data_required": ["intent"],
                                "agent_message": f"You already have an existing {_acct.replace('_', ' ').title()} account associated with this email or phone number. You cannot open a duplicate account of the same type."
                            }
                    else:
                        return {
                            "ui_action": "RENDER_CHAT",
                            "session_ulid": session_ulid,
                            "data_required": ["intent"],
                            "agent_message": f"You already have an existing {_acct.replace('_', ' ').title()} account associated with this email or phone number. You cannot open a duplicate account of the same type."
                        }
            else:
                logger.info(f"[OnboardAI][INTENT_DEBUG] Step 3: No intent found yet.")
                
            # If still no account type, ask the user
            if not user_obj.account_type:
                logger.info(f"[OnboardAI][STATE] Step 3 incomplete: account_type not set")
                return {
                    "ui_action": "RENDER_CHAT",
                    "session_ulid": session_ulid,
                    "data_required": ["intent"],
                    "agent_message": (
                        "Your contact information is verified! "
                        "What would you like to do today? You can open a new account (Retail, Digital-Only, or SME) "
                        "or perform Account Reactivation / Re-KYC if you are an existing customer."
                    ),
                }
            else:
                # Refresh _acct for step 4
                _acct = user_obj.account_type

        # Step 4 — KYC documents (Dynamic based on account type)
        _kyc_done = bool(user_obj.pan_id and user_obj.aadhar_id)
        
        # ── Requirement 3: Lifecycle Force-Reset ──
        if _lifecycle_intent:
            _kyc_done = resolved_state.get("kycUploaded", False)
            logger.info(f"[OnboardAI][LIFECYCLE] Step 4 check: _kyc_done={_kyc_done} (forced via resolved_state)")

        _gst_done = True
        
        # Define required documents dynamically
        required_docs = ["aadhaar", "pan"]
        if _acct == "sme_current":
            required_docs.append("gst")
            _ai      = user_obj.additional_info
            _ai_data = _ai.data if _ai and isinstance(getattr(_ai, "data", None), dict) else {}
            _gst_done = bool(_ai_data.get("gstin") or _ai_data.get("gst_data"))
            if _lifecycle_intent:
                # Force re-check of GST for SME lifecycle flows if needed
                # (Assuming GST might also need re-validation)
                _gst_done = resolved_state.get("kycUploaded", False) 
            
        if not _kyc_done or not _gst_done:
            logger.info(f"[OnboardAI][STATE] Step 4 incomplete: kyc={_kyc_done} gst={_gst_done}")
            msg = "Please upload your Aadhaar, PAN, and GST Certificate to continue." if _acct == "sme_current" else "Please upload your Aadhaar and PAN documents to continue."
            return {
                "ui_action": "RENDER_KYC_UPLOAD",
                "session_ulid": session_ulid,
                "data_required": required_docs,
                "agent_message": msg,
            }

        # Step 5 — Face verification
        if not resolved_state.get("faceVerified"):
            logger.info(f"[OnboardAI][STATE] Step 5 incomplete: face not verified")
            return {
                "ui_action": "RENDER_FACE_VERIFICATION",
                "session_ulid": session_ulid,
                "data_required": [],
                "agent_message": "Please complete face verification to continue.",
            }

        # Step 6 — Additional info (ONLY fires when all above steps are done)
        # Deep-check: for SME accounts, the row must contain user-submitted form
        # fields, not just the gst_data written during the KYC phase.
        _ai_row    = user_obj.additional_info  # already loaded via selectinload
        _ai_data   = _ai_row.data if _ai_row and isinstance(getattr(_ai_row, "data", None), dict) else {}
        _SME_FORM_KEYS = {"business_profile", "stakeholders"}
        if _is_sme_ses:
            _add_info_done = bool(_SME_FORM_KEYS & set(_ai_data.keys()))
        else:
            _add_info_done = bool(_ai_data)  # retail: any non-empty row is sufficient
        
        # [NEW]: Digital-Only Bypass
        if _acct == "digital_only":
            logger.info(f"[OnboardAI][STATE] Digital-Only detected: bypassing Step 6")
            _add_info_done = True

        if not _add_info_done:
            logger.info(f"[OnboardAI][STATE] Step 6 incomplete: additional info form not submitted (sme={_is_sme_ses}, ai_keys={list(_ai_data.keys())})")
            from app.services.additional_info_service import get_form_schema
            schema   = get_form_schema(_acct or "retail_savings")
            info_msg = (
                "To complete your SME account, please provide your business details below."
                if _is_sme_ses else
                "Please complete the final regulatory details to finish your account opening."
            )
            return {
                "ui_action": "RENDER_ADDITIONAL_INFO_FORM",
                "session_ulid": session_ulid,
                "data_required": schema,
                "agent_message": info_msg,
                "extracted_data": {
                    "account_type": _acct or "retail_savings",
                    "form_schema": schema
                }
            }

        # Step 7 — All complete
        # ── Guard: Prevent duplicate risk evaluation on re-sent messages ──
        _current_status = getattr(user_obj, "status", None) if user_obj else None
        _terminal_states = {"approved", "APPROVED", "rejected", "REJECTED", "MANUAL_REVIEW", "UNDER_REVIEW"}
        if _current_status in _terminal_states:
            logger.info(f"[OnboardAI][STATE] Session {session_ulid} already in terminal state '{_current_status}' — skipping re-evaluation")
            if _current_status in ("MANUAL_REVIEW", "UNDER_REVIEW"):
                return {
                    "ui_action": "RENDER_HUMAN_REVIEW",
                    "session_ulid": session_ulid,
                    "data_required": [],
                    "agent_message": (
                        "Your application is currently under review by our operations team. "
                        "You will be contacted within 24-48 hours with a decision."
                    ),
                }
            elif _current_status in ("approved", "APPROVED"):
                return {
                    "ui_action": "RENDER_AUTO_APPROVE",
                    "session_ulid": session_ulid,
                    "data_required": [],
                    "agent_message": "Your account has already been approved! You're all set.",
                }
            else:  # rejected or REJECTED
                return {
                    "ui_action": "RENDER_CHAT",
                    "session_ulid": session_ulid,
                    "data_required": [],
                    "agent_message": "This application was not approved. Please contact support if you believe this is an error.",
                }

        logger.info(f"[OnboardAI][STATE] All steps complete -> running risk evaluation")

        # ── Phase 4 Hook: Risk evaluation BEFORE final success (State Evaluator fallthrough) ──
        _ai_final   = user_obj.additional_info
        _ai_payload = _ai_final.data if _ai_final and isinstance(getattr(_ai_final, "data", None), dict) else {}
        _fv_raw     = await redis_client.get(f"face_verification:{session_ulid}")
        _fv_res     = json.loads(_fv_raw) if _fv_raw else {}
        _risk_routing = await _apply_risk_routing(
            user=user_obj, db=db, session_ulid=session_ulid,
            final_data=_ai_payload, face_result=_fv_res,
            current_state=current_state
        )
        if _risk_routing["_risk_action"] != "PROCEED":
            return _risk_routing

        logger.info(f"[OnboardAI][STATE] Risk APPROVED -> RENDER_AUTO_APPROVE")
        return {
            "ui_action": "RENDER_AUTO_APPROVE",
            "session_ulid": session_ulid,
            "data_required": [],
            "agent_message": "All steps are complete! Your application is being processed.",
        }


    # Configure Gemini explicitly with the active key avoiding cache issues
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-3.1-flash-lite-preview",
        tools=tool_registry_schemas,
        system_instruction=SYSTEM_INSTRUCTION
    )

    # Prompt Construction
    prompt = f"""
    Current Session ULID: {session_ulid}
    Current User State: {json.dumps(resolved_state)}
    Document Data Extracted: {bool(resolved_state.get('kycUploaded', False))}
    Face Verification Status: {bool(resolved_state.get('faceVerified', False))}
    Current Account Type: {resolved_state.get('intent', 'NOT_SELECTED')}
    User Chat Message: "{message}"
    """
    
    if "SYSTEM: DOCUMENTS_UPLOADED_SUCCESSFULLY" in message:
        prompt += "\n[SYSTEM OVERRIDE]: Phase 3 Hook. Documents uploaded. MUST execute extract_and_review_tool directly. The tool will return a processing status. You MUST return RENDER_PROCESSING to the UI to display the loading screen."
        
    if "USER_CONFIRMED_DATA" in message:
        prompt += f"\n[SYSTEM OVERRIDE]: User confirmed their edits. MUST execute trigger_face_verification_tool to proceed to the next security step."

    if "SYSTEM: FACE_VERIFICATION_SUCCESSFUL" in message:
        prompt += f"\n[SYSTEM OVERRIDE]: Face verification succeeded. MUST execute execute_hybrid_freeze_tool feeding it the final_data payload natively via execution layer."

    prompt += "\nDecide the next action or execute a tool. Remember to format the final output as the strict JSON schema requested."
    # 1. Trigger Initial AI Decision
    try:
        chat = model.start_chat()
        response = await chat.send_message_async(prompt)
        
        # 2. Multi-turn Function Call Loop
        for _ in range(3):
            if not getattr(response, "parts", None) or not response.parts[0].function_call:
                break
                
            fc = response.parts[0].function_call
            
            # Execute the Python Function seamlessly
            tool_result = await handle_tool_call(fc, session_ulid or "", db, final_data=final_data)
            
            # Short-circuit Native Tool states: Immediately bypass Secondary LLM 
            # rendering to prevent cascading AI Rate Limit crashes/timeouts OR to enable fast UI return.
            if isinstance(tool_result, dict) and "ui_action" in tool_result:
                if tool_result.get("status") == "processing":
                    logger.info(f"[OnboardAI] Fast-Tracking Processing Hook: {tool_result}")
                    return {
                        "ui_action": tool_result["ui_action"],
                        "session_ulid": session_ulid,
                        "data_required": [],
                        "agent_message": tool_result.get("message", "Processing documents...")
                    }
                elif "error" in tool_result:
                    logger.error(f"[OnboardAI] Fast-Tracking Tool Failure Callback: {tool_result}")
                    return {
                        "ui_action": tool_result["ui_action"],
                        "extracted_data": {},
                        # Standardizing fallback message exactly as requested
                        "agent_message": "Extraction failed, please try again."
                    }
            
            # Pass the execution output continuously back to Gemini to get the final JSON response
            response = await chat.send_message_async(
                {"function_response": {"name": fc.name, "response": {"result": tool_result}}}
            )

        # 3. Parse and Return the strictly sanitized UI command
        raw_text = response.text
        # Optional scrubbing if it wrapped the output in markdown code blocks natively 
        import re
        clean_text = re.sub(r'```(?:json)?\n(.*?)```', r'\1', raw_text, flags=re.DOTALL).strip()
        clean_json = json.loads(clean_text)
        
        # Deterministic Native Injection hook to prevent LLM hallucination of schema
        if clean_json.get("ui_action") == "RENDER_DATA_REVIEW":
            from app.db.redis_client import get_temp_extraction
            temp_data = get_temp_extraction(session_ulid)
            if temp_data and "validation" in temp_data:
                v = temp_data["validation"]
                # Always inject the FULL validation block — never just combined_data
                clean_json["extracted_data"] = {
                    "combined_data": dict(v.get("combined_data", {})),
                    "gst_data":      dict(v.get("gst_data", {})),
                    "valid":          bool(v.get("valid", True)),
                    "flags":          list(v.get("flags", [])),
                }
        
        # --- Task 4/Constraint: Explicitly extract account_type and commit to postgres ---
        if clean_json.get("extracted_data") and "account_type" in clean_json["extracted_data"]:
            new_intent = clean_json["extracted_data"]["account_type"]
            logger.info(f"[OnboardAI][INTENT] ⚡ Detected intent in LLM output: {new_intent}. Committing to Database.")
            
            # Re-fetch user to ensure we are using the latest session record for the commit
            result_u = await db.execute(select(UserInitial).where(UserInitial.id == session_ulid))
            user_u = result_u.scalar_one_or_none()
            if user_u:
                try:
                    user_u.account_type = new_intent
                    await db.commit()
                    current_intent = new_intent # Update local variable for safety gate
                    logger.info(f"[OnboardAI][INTENT] ✓ Database successfully synchronized with account_type: {new_intent}")
                except Exception as e:
                    await db.rollback()
                    logger.error(f"[OnboardAI][INTENT] DB Commit failed (Duplicate account): {e}")
                    
                    _existing_q = select(UserInitial).where(
                        (UserInitial.phone == user_u.phone) &
                        (UserInitial.account_type == new_intent)
                    )
                    existing_res = await db.execute(_existing_q)
                    existing_draft = existing_res.scalar_one_or_none()
                    if not existing_draft and user_u.email:
                        _existing_q2 = select(UserInitial).where(
                            (UserInitial.email == user_u.email) &
                            (UserInitial.account_type == new_intent)
                        )
                        existing_res = await db.execute(_existing_q2)
                        existing_draft = existing_res.scalar_one_or_none()

                    if existing_draft and existing_draft.status == 'draft' and existing_draft.id != session_ulid:
                        logger.info(f"[OnboardAI][INTENT] Deleting conflicting draft {existing_draft.id} to allow new session {session_ulid}")
                        await db.delete(existing_draft)
                        await db.commit()
                        try:
                            user_u.account_type = new_intent
                            await db.commit()
                            current_intent = new_intent
                        except Exception as retry_e:
                            await db.rollback()
                            return {
                                "ui_action": "RENDER_CHAT",
                                "session_ulid": session_ulid,
                                "data_required": ["intent"],
                                "agent_message": f"You already have an existing {new_intent.replace('_', ' ').title()} account associated with this email or phone number. You cannot open a duplicate account of the same type."
                            }
                    else:
                        return {
                            "ui_action": "RENDER_CHAT",
                            "session_ulid": session_ulid,
                            "data_required": ["intent"],
                            "agent_message": f"You already have an existing {new_intent.replace('_', ' ').title()} account associated with this email or phone number. You cannot open a duplicate account of the same type."
                        }
        
        # ── SAFETY GATE: Enforce Account Selection ─────────────────────────────
        # Fetch LATEST intent from Redis to avoid stale 'resolved_state' blocking progression
        current_intent = resolved_state.get("intent")
        if not current_intent and session_ulid:
            from app.db.redis_client import redis_client as sync_redis
            current_intent = sync_redis.get(f"session_intent:{session_ulid}")

        if clean_json.get("ui_action") == "RENDER_KYC_UPLOAD" and not current_intent:
            logger.warning(f"[OnboardAI][SAFETY] LLM tried to skip intent for session {session_ulid}. Forcing RENDER_CHAT.")
            return {
                "ui_action": "RENDER_CHAT",
                "agent_message": "Email verified! Before we proceed to document upload, what type of account would you like to open today? (Options: Retail, Digital-Only, or SME)",
                "data_required": ["intent"],
                "session_ulid": session_ulid
            }
        
        clean_json["session_ulid"] = session_ulid # Inject session_ulid into the response dictionary
        return clean_json
        
    except Exception as e:
        logger.exception(f"Failed to parse Agentic JSON Orchestration output: {e}")
        # Catch-all graceful boundary specifically for document upload flow
        if "SYSTEM: DOCUMENTS_UPLOADED_SUCCESSFULLY" in message:
            return {
                "ui_action": "RENDER_KYC_UPLOAD",
                "extracted_data": {},
                "agent_message": "Extraction failed, please try again.",
                "session_ulid": session_ulid
            }
        
        return {
            "ui_action": "RENDER_CHAT",
            "agent_message": f"An internal orchestration error occurred: {str(e)}",
            "data_required": [],
            "session_ulid": session_ulid
        }