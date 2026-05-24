"""
app/services/email_service.py
==============================
Transactional email notifications for the bank onboarding flow.

Functions
---------
send_approval_email  — fired when an application is auto-approved or manually approved
send_review_email    — fired when an application is escalated to manual review

Both functions are designed to be launched as fire-and-forget tasks via
asyncio.create_task() so they never block the main onboarding response.

Email infrastructure is shared with otp_service.py:
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS  from app.config.settings
    Port 465  → use_tls=True  (Implicit TLS)
    Port 587  → start_tls=True (STARTTLS)
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
from typing import Optional

import aiosmtplib
from loguru import logger

from app.config import settings


# ---------------------------------------------------------------------------
# Internal helper: build SMTP kwargs from settings
# ---------------------------------------------------------------------------

def _smtp_kwargs() -> dict:
    kwargs: dict = dict(
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASS,
    )
    if settings.SMTP_PORT == 465:
        kwargs["use_tls"] = True
    else:
        kwargs["start_tls"] = True
    return kwargs


def _format_account_type(raw: str) -> str:
    """Convert internal account_type to a human-readable label."""
    labels = {
        "retail_savings": "Retail Savings Account",
        "sme_current":    "SME Current Account",
        "digital_only":   "Digital Savings Account",
    }
    return labels.get(raw, raw.replace("_", " ").title())


# ---------------------------------------------------------------------------
# 1. Auto-approval / Manual-approval Email
# ---------------------------------------------------------------------------

async def send_approval_email(
    email: str,
    name: str,
    application_id: str,
    account_number: str,
    branch_name: str,
    branch_address: str,
    ifsc: str,
    account_type: str,
    manager_name: Optional[str] = None,
    relationship_officer: Optional[str] = None,
) -> None:
    """
    Send a beautiful HTML approval email with an animated green SVG tick.
    All parameters are non-PII display data; no raw KYC fields are included.
    """
    if not (settings.SMTP_USER and settings.SMTP_PASS):
        logger.warning("[EmailService] SMTP not configured — skipping approval email.")
        return

    display_name = name or "Valued Customer"
    acct_label   = _format_account_type(account_type)
    short_app_id = application_id[:24] + "…" if len(application_id) > 24 else application_id

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Account Created Successfully</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #f0f4f8;
    font-family: 'Inter', Arial, sans-serif;
    padding: 32px 16px;
  }}
  .wrapper {{
    max-width: 560px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 20px;
    overflow: hidden;
    box-shadow: 0 4px 32px rgba(0,0,0,0.10);
  }}
  /* ── Header banner ── */
  .header {{
    background: linear-gradient(135deg, #0d9e6e 0%, #06b87a 100%);
    padding: 36px 32px 28px;
    text-align: center;
  }}
  .header h1 {{
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
    margin-top: 12px;
    letter-spacing: -0.3px;
  }}
  .header p {{
    color: rgba(255,255,255,0.85);
    font-size: 14px;
    margin-top: 6px;
  }}

  /* ── Animated green tick SVG ── */
  .tick-wrap {{
    display: flex;
    justify-content: center;
    margin: 0 auto;
  }}
  .tick-circle {{
    stroke: #ffffff;
    stroke-width: 3;
    fill: none;
    stroke-dasharray: 283;
    stroke-dashoffset: 283;
    animation: drawCircle 0.6s ease forwards;
    animation-delay: 0.1s;
  }}
  .tick-check {{
    stroke: #ffffff;
    stroke-width: 3.5;
    fill: none;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-dasharray: 80;
    stroke-dashoffset: 80;
    animation: drawCheck 0.45s ease forwards;
    animation-delay: 0.65s;
  }}
  @keyframes drawCircle {{
    to {{ stroke-dashoffset: 0; }}
  }}
  @keyframes drawCheck {{
    to {{ stroke-dashoffset: 0; }}
  }}

  /* ── Body ── */
  .body {{ padding: 32px; }}
  .greeting {{
    font-size: 16px;
    color: #1a2b4a;
    margin-bottom: 8px;
    font-weight: 600;
  }}
  .sub {{
    font-size: 14px;
    color: #4a5568;
    margin-bottom: 28px;
    line-height: 1.6;
  }}

  /* ── Detail card ── */
  .detail-card {{
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 28px;
  }}
  .detail-card-title {{
    background: #edf7f2;
    padding: 10px 16px;
    font-size: 12px;
    font-weight: 700;
    color: #0d9e6e;
    text-transform: uppercase;
    letter-spacing: 0.8px;
  }}
  .detail-row {{
    display: flex;
    padding: 11px 16px;
    border-bottom: 1px solid #e2e8f0;
  }}
  .detail-row:last-child {{ border-bottom: none; }}
  .detail-label {{
    width: 40%;
    font-size: 12px;
    color: #718096;
    font-weight: 500;
  }}
  .detail-value {{
    width: 60%;
    font-size: 13px;
    color: #1a2b4a;
    font-weight: 600;
    word-break: break-all;
  }}
  .account-no {{
    font-size: 16px;
    color: #0d9e6e;
    letter-spacing: 2px;
    font-weight: 700;
  }}

  /* ── Footer ── */
  .footer {{
    background: #f8fafc;
    padding: 20px 32px;
    text-align: center;
    border-top: 1px solid #e2e8f0;
  }}
  .footer p {{
    font-size: 12px;
    color: #718096;
    line-height: 1.6;
  }}
  .brand {{
    font-weight: 700;
    color: #0d9e6e;
  }}
</style>
</head>
<body>
<div class="wrapper">

  <!-- Header with animated tick -->
  <div class="header">
    <div class="tick-wrap">
      <svg width="80" height="80" viewBox="0 0 100 100">
        <circle class="tick-circle" cx="50" cy="50" r="45"/>
        <polyline class="tick-check" points="28,52 44,68 72,34"/>
      </svg>
    </div>
    <h1>Account Created Successfully!</h1>
    <p>Welcome to the family, {display_name} 🎉</p>
  </div>

  <!-- Body -->
  <div class="body">
    <p class="greeting">Dear {display_name},</p>
    <p class="sub">
      We are delighted to inform you that your bank account application has been
      <strong>approved and your account is now active</strong>. Below are your
      account details for your records.
    </p>

    <!-- Account Details Card -->
    <div class="detail-card">
      <div class="detail-card-title">Account Details</div>
      <div class="detail-row">
        <span class="detail-label">Account Number</span>
        <span class="detail-value account-no">{account_number}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Account Type</span>
        <span class="detail-value">{acct_label}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Application No.</span>
        <span class="detail-value" style="font-size:11px; letter-spacing:0.5px;">{short_app_id}</span>
      </div>
    </div>

    <!-- Branch Details Card -->
    <div class="detail-card">
      <div class="detail-card-title">Branch Details</div>
      <div class="detail-row">
        <span class="detail-label">Branch Name</span>
        <span class="detail-value">{branch_name}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">IFSC Code</span>
        <span class="detail-value" style="font-family: monospace;">{ifsc}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Branch Address</span>
        <span class="detail-value">{branch_address}</span>
      </div>
      {"" if not manager_name else f'''
      <div class="detail-row">
        <span class="detail-label">Branch Manager</span>
        <span class="detail-value">{manager_name}</span>
      </div>'''}
      {"" if not relationship_officer else f'''
      <div class="detail-row">
        <span class="detail-label">Relationship Officer</span>
        <span class="detail-value">{relationship_officer}</span>
      </div>'''}
    </div>

    <p class="sub" style="text-align:center; color:#0d9e6e; font-weight:600;">
      Thank you for choosing us. Your financial journey begins now! 🚀
    </p>
  </div>

  <!-- Footer -->
  <div class="footer">
    <p>
      This is an automated message from <span class="brand">OnboardAI Banking</span>.<br />
      Please do not reply to this email. For support, contact your branch manager.
    </p>
  </div>

</div>
</body>
</html>"""

    plain = (
        f"Dear {display_name},\n\n"
        f"Your bank account has been successfully created.\n\n"
        f"Account Number  : {account_number}\n"
        f"Account Type    : {acct_label}\n"
        f"Application No. : {application_id}\n"
        f"Branch Name     : {branch_name}\n"
        f"IFSC Code       : {ifsc}\n"
        f"Branch Address  : {branch_address}\n\n"
        f"Thank you for choosing OnboardAI Banking."
    )

    msg = EmailMessage()
    msg["From"]    = settings.SMTP_USER
    msg["To"]      = email
    msg["Subject"] = "🎉 Your Bank Account Has Been Successfully Created!"
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
        logger.info("[EmailService] Approval email sent to %s", email)
    except Exception as exc:
        logger.error("[EmailService] Failed to send approval email: %s", exc)


# ---------------------------------------------------------------------------
# 2. Manual Review Escalation Email
# ---------------------------------------------------------------------------

async def send_review_email(
    email: str,
    name: str,
    application_id: str,
) -> None:
    """
    Send a brief HTML email informing the user their application is under manual review.
    Only the application number is disclosed — no account/branch details.
    """
    if not (settings.SMTP_USER and settings.SMTP_PASS):
        logger.warning("[EmailService] SMTP not configured — skipping review email.")
        return

    display_name = name or "Valued Customer"
    short_app_id = application_id[:24] + "…" if len(application_id) > 24 else application_id

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Application Under Review</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #f0f4f8;
    font-family: 'Inter', Arial, sans-serif;
    padding: 32px 16px;
  }}
  .wrapper {{
    max-width: 560px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 20px;
    overflow: hidden;
    box-shadow: 0 4px 32px rgba(0,0,0,0.10);
  }}

  /* ── Header banner ── */
  .header {{
    background: linear-gradient(135deg, #d97706 0%, #f59e0b 100%);
    padding: 36px 32px 28px;
    text-align: center;
  }}
  .header h1 {{
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
    margin-top: 12px;
    letter-spacing: -0.3px;
  }}
  .header p {{
    color: rgba(255,255,255,0.88);
    font-size: 14px;
    margin-top: 6px;
  }}

  /* ── Animated yellow warning SVG ── */
  .warn-wrap {{
    display: flex;
    justify-content: center;
    margin: 0 auto;
  }}
  .warn-triangle {{
    stroke: #ffffff;
    stroke-width: 3;
    fill: none;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-dasharray: 280;
    stroke-dashoffset: 280;
    animation: drawTriangle 0.7s ease forwards;
    animation-delay: 0.1s;
  }}
  .warn-bang {{
    stroke: #ffffff;
    stroke-width: 3.5;
    fill: none;
    stroke-linecap: round;
    stroke-dasharray: 30;
    stroke-dashoffset: 30;
    animation: drawBang 0.3s ease forwards;
    animation-delay: 0.75s;
  }}
  .warn-dot {{
    fill: #ffffff;
    opacity: 0;
    animation: fadeIn 0.2s ease forwards;
    animation-delay: 1.05s;
  }}
  @keyframes drawTriangle {{
    to {{ stroke-dashoffset: 0; }}
  }}
  @keyframes drawBang {{
    to {{ stroke-dashoffset: 0; }}
  }}
  @keyframes fadeIn {{
    to {{ opacity: 1; }}
  }}

  /* ── Body ── */
  .body {{ padding: 32px; }}
  .greeting {{
    font-size: 16px;
    color: #1a2b4a;
    margin-bottom: 8px;
    font-weight: 600;
  }}
  .sub {{
    font-size: 14px;
    color: #4a5568;
    margin-bottom: 28px;
    line-height: 1.7;
  }}

  /* ── App ID card ── */
  .app-card {{
    background: #fffbeb;
    border: 1.5px solid #fcd34d;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 24px;
    text-align: center;
  }}
  .app-card-label {{
    font-size: 11px;
    font-weight: 700;
    color: #92400e;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 8px;
  }}
  .app-card-value {{
    font-size: 13px;
    font-weight: 700;
    color: #78350f;
    letter-spacing: 0.5px;
    word-break: break-all;
    font-family: monospace;
  }}

  /* ── Status steps ── */
  .steps {{
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-bottom: 28px;
  }}
  .step {{
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 14px;
    background: #f8fafc;
    border-radius: 8px;
    border-left: 3px solid #f59e0b;
  }}
  .step-icon {{ font-size: 16px; }}
  .step-text {{ font-size: 13px; color: #4a5568; line-height: 1.5; }}
  .step-text strong {{ color: #1a2b4a; }}

  /* ── Footer ── */
  .footer {{
    background: #f8fafc;
    padding: 20px 32px;
    text-align: center;
    border-top: 1px solid #e2e8f0;
  }}
  .footer p {{
    font-size: 12px;
    color: #718096;
    line-height: 1.6;
  }}
  .brand {{ font-weight: 700; color: #d97706; }}
</style>
</head>
<body>
<div class="wrapper">

  <!-- Header with animated warning triangle -->
  <div class="header">
    <div class="warn-wrap">
      <svg width="80" height="80" viewBox="0 0 100 100">
        <!-- Triangle outline -->
        <polygon class="warn-triangle" points="50,14 90,80 10,80"/>
        <!-- Exclamation line -->
        <line class="warn-bang" x1="50" y1="36" x2="50" y2="62"/>
        <!-- Exclamation dot -->
        <circle class="warn-dot" cx="50" cy="71" r="3"/>
      </svg>
    </div>
    <h1>Application Under Review</h1>
    <p>We're carefully reviewing your details</p>
  </div>

  <!-- Body -->
  <div class="body">
    <p class="greeting">Dear {display_name},</p>
    <p class="sub">
      Thank you for submitting your account application with us. Our compliance team
      has flagged your application for a <strong>manual review</strong> as part of
      our standard due-diligence process. Please have patience — this is routine
      and does not necessarily indicate an issue with your application.
    </p>

    <!-- Application ID -->
    <div class="app-card">
      <div class="app-card-label">Your Application Number</div>
      <div class="app-card-value">{short_app_id}</div>
    </div>

    <!-- What happens next -->
    <div class="steps">
      <div class="step">
        <span class="step-icon">🔍</span>
        <span class="step-text">
          <strong>Review in progress</strong><br/>
          Our compliance team will review your documents within 2–3 business days.
        </span>
      </div>
      <div class="step">
        <span class="step-icon">📧</span>
        <span class="step-text">
          <strong>You will be notified</strong><br/>
          We will send you an email as soon as a decision is made.
        </span>
      </div>
      <div class="step">
        <span class="step-icon">📞</span>
        <span class="step-text">
          <strong>Need help?</strong><br/>
          Contact your relationship officer or visit your nearest branch.
        </span>
      </div>
    </div>

    <p class="sub" style="text-align:center; color:#92400e;">
      Please keep your Application Number safe for future reference.
    </p>
  </div>

  <!-- Footer -->
  <div class="footer">
    <p>
      This is an automated message from <span class="brand">OnboardAI Banking</span>.<br/>
      Please do not reply to this email. For queries, quote your Application Number.
    </p>
  </div>

</div>
</body>
</html>"""

    plain = (
        f"Dear {display_name},\n\n"
        f"Your application is being reviewed by our team. Please have patience.\n\n"
        f"Application No.: {application_id}\n\n"
        f"We will notify you within 2-3 business days.\n\n"
        f"— OnboardAI Banking"
    )

    msg = EmailMessage()
    msg["From"]    = settings.SMTP_USER
    msg["To"]      = email
    msg["Subject"] = "⏳ Your Application is Under Review — OnboardAI Banking"
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
        logger.info("[EmailService] Review email sent to %s", email)
    except Exception as exc:
        logger.error("[EmailService] Failed to send review email: %s", exc)


# ---------------------------------------------------------------------------
# 3. Manual Rejection Email
# ---------------------------------------------------------------------------

async def send_rejection_email(
    email: str,
    name: str,
    application_id: str,
) -> None:
    """
    Send a polite HTML email informing the user their application is rejected.
    Uses an animated Red Cross SVG.
    """
    if not (settings.SMTP_USER and settings.SMTP_PASS):
        logger.warning("[EmailService] SMTP not configured — skipping rejection email.")
        return

    display_name = name or "Applicant"
    short_app_id = application_id[:24] + "…" if len(application_id) > 24 else application_id

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Application Update</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #f0f4f8;
    font-family: 'Inter', Arial, sans-serif;
    padding: 32px 16px;
  }}
  .wrapper {{
    max-width: 560px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 20px;
    overflow: hidden;
    box-shadow: 0 4px 32px rgba(0,0,0,0.10);
  }}

  /* ── Header banner ── */
  .header {{
    background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
    padding: 36px 32px 28px;
    text-align: center;
  }}
  .header h1 {{
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
    margin-top: 12px;
    letter-spacing: -0.3px;
  }}
  .header p {{
    color: rgba(255,255,255,0.88);
    font-size: 14px;
    margin-top: 6px;
  }}

  /* ── Animated Red Cross SVG ── */
  .cross-wrap {{
    display: flex;
    justify-content: center;
    margin: 0 auto;
  }}
  .cross-circle {{
    stroke: #ffffff;
    stroke-width: 3;
    fill: none;
    stroke-dasharray: 283;
    stroke-dashoffset: 283;
    animation: drawCircle 0.6s ease forwards;
    animation-delay: 0.1s;
  }}
  .cross-line {{
    stroke: #ffffff;
    stroke-width: 3.5;
    fill: none;
    stroke-linecap: round;
    stroke-dasharray: 40;
    stroke-dashoffset: 40;
    animation: drawLine 0.3s ease forwards;
  }}
  .cross-line1 {{ animation-delay: 0.6s; }}
  .cross-line2 {{ animation-delay: 0.8s; }}
  
  @keyframes drawCircle {{
    to {{ stroke-dashoffset: 0; }}
  }}
  @keyframes drawLine {{
    to {{ stroke-dashoffset: 0; }}
  }}

  /* ── Body ── */
  .body {{ padding: 32px; }}
  .greeting {{
    font-size: 16px;
    color: #1a2b4a;
    margin-bottom: 8px;
    font-weight: 600;
  }}
  .sub {{
    font-size: 14px;
    color: #4a5568;
    margin-bottom: 28px;
    line-height: 1.7;
  }}

  /* ── App ID card ── */
  .app-card {{
    background: #fef2f2;
    border: 1.5px solid #fca5a5;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 24px;
    text-align: center;
  }}
  .app-card-label {{
    font-size: 11px;
    font-weight: 700;
    color: #991b1b;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 8px;
  }}
  .app-card-value {{
    font-size: 13px;
    font-weight: 700;
    color: #7f1d1d;
    letter-spacing: 0.5px;
    word-break: break-all;
    font-family: monospace;
  }}

  /* ── Footer ── */
  .footer {{
    background: #f8fafc;
    padding: 20px 32px;
    text-align: center;
    border-top: 1px solid #e2e8f0;
  }}
  .footer p {{
    font-size: 12px;
    color: #718096;
    line-height: 1.6;
  }}
  .brand {{ font-weight: 700; color: #dc2626; }}
</style>
</head>
<body>
<div class="wrapper">

  <!-- Header -->
  <div class="header">
    <div class="cross-wrap">
      <svg width="80" height="80" viewBox="0 0 100 100">
        <circle class="cross-circle" cx="50" cy="50" r="45"/>
        <line class="cross-line cross-line1" x1="35" y1="35" x2="65" y2="65"/>
        <line class="cross-line cross-line2" x1="65" y1="35" x2="35" y2="65"/>
      </svg>
    </div>
    <h1>Application Update</h1>
    <p>Important information regarding your application</p>
  </div>

  <!-- Body -->
  <div class="body">
    <p class="greeting">Dear {display_name},</p>
    <p class="sub">
      Thank you for your interest in OnboardAI Banking. We have carefully reviewed your application.
      Unfortunately, we regret to inform you that we cannot approve your application at this time.
    </p>

    <!-- Application ID -->
    <div class="app-card">
      <div class="app-card-label">Your Application Number</div>
      <div class="app-card-value">{short_app_id}</div>
    </div>

    <p class="sub">
      Our decision was made after a thorough review process. We understand this may be disappointing,
      and we thank you for considering us for your banking needs.
    </p>
  </div>

  <!-- Footer -->
  <div class="footer">
    <p>
      This is an automated message from <span class="brand">OnboardAI Banking</span>.<br/>
      Please do not reply to this email.
    </p>
  </div>

</div>
</body>
</html>"""

    plain = (
        f"Dear {display_name},\n\n"
        f"We have carefully reviewed your application. Unfortunately, we regret to inform you that we cannot approve your application at this time.\n\n"
        f"Application No.: {application_id}\n\n"
        f"We thank you for your interest in OnboardAI Banking.\n\n"
        f"— OnboardAI Banking"
    )

    msg = EmailMessage()
    msg["From"]    = settings.SMTP_USER
    msg["To"]      = email
    msg["Subject"] = "Update Regarding Your Application — OnboardAI Banking"
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
        logger.info("[EmailService] Rejection email sent to %s", email)
    except Exception as exc:
        logger.error("[EmailService] Failed to send rejection email: %s", exc)


# ---------------------------------------------------------------------------
# 4. Auto Rejection Email
# ---------------------------------------------------------------------------

async def send_auto_rejection_email(
    email: str,
    name: str,
    application_id: str,
) -> None:
    """
    Send a polite HTML email informing the user their application is auto rejected.
    Uses an animated Red Cross SVG.
    """
    if not (settings.SMTP_USER and settings.SMTP_PASS):
        logger.warning("[EmailService] SMTP not configured — skipping auto rejection email.")
        return

    display_name = name or "Applicant"
    short_app_id = application_id[:24] + "…" if len(application_id) > 24 else application_id

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Application Update</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #f0f4f8;
    font-family: 'Inter', Arial, sans-serif;
    padding: 32px 16px;
  }}
  .wrapper {{
    max-width: 560px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 20px;
    overflow: hidden;
    box-shadow: 0 4px 32px rgba(0,0,0,0.10);
  }}

  /* ── Header banner ── */
  .header {{
    background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
    padding: 36px 32px 28px;
    text-align: center;
  }}
  .header h1 {{
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
    margin-top: 12px;
    letter-spacing: -0.3px;
  }}
  .header p {{
    color: rgba(255,255,255,0.88);
    font-size: 14px;
    margin-top: 6px;
  }}

  /* ── Animated Red Cross SVG ── */
  .cross-wrap {{
    display: flex;
    justify-content: center;
    margin: 0 auto;
  }}
  .cross-circle {{
    stroke: #ffffff;
    stroke-width: 3;
    fill: none;
    stroke-dasharray: 283;
    stroke-dashoffset: 283;
    animation: drawCircle 0.6s ease forwards;
    animation-delay: 0.1s;
  }}
  .cross-line {{
    stroke: #ffffff;
    stroke-width: 3.5;
    fill: none;
    stroke-linecap: round;
    stroke-dasharray: 40;
    stroke-dashoffset: 40;
    animation: drawLine 0.3s ease forwards;
  }}
  .cross-line1 {{ animation-delay: 0.6s; }}
  .cross-line2 {{ animation-delay: 0.8s; }}
  
  @keyframes drawCircle {{
    to {{ stroke-dashoffset: 0; }}
  }}
  @keyframes drawLine {{
    to {{ stroke-dashoffset: 0; }}
  }}

  /* ── Body ── */
  .body {{ padding: 32px; }}
  .greeting {{
    font-size: 16px;
    color: #1a2b4a;
    margin-bottom: 8px;
    font-weight: 600;
  }}
  .sub {{
    font-size: 14px;
    color: #4a5568;
    margin-bottom: 28px;
    line-height: 1.7;
  }}

  /* ── App ID card ── */
  .app-card {{
    background: #fef2f2;
    border: 1.5px solid #fca5a5;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 24px;
    text-align: center;
  }}
  .app-card-label {{
    font-size: 11px;
    font-weight: 700;
    color: #991b1b;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 8px;
  }}
  .app-card-value {{
    font-size: 13px;
    font-weight: 700;
    color: #7f1d1d;
    letter-spacing: 0.5px;
    word-break: break-all;
    font-family: monospace;
  }}

  /* ── Footer ── */
  .footer {{
    background: #f8fafc;
    padding: 20px 32px;
    text-align: center;
    border-top: 1px solid #e2e8f0;
  }}
  .footer p {{
    font-size: 12px;
    color: #718096;
    line-height: 1.6;
  }}
  .brand {{ font-weight: 700; color: #dc2626; }}
</style>
</head>
<body>
<div class="wrapper">

  <!-- Header -->
  <div class="header">
    <div class="cross-wrap">
      <svg width="80" height="80" viewBox="0 0 100 100">
        <circle class="cross-circle" cx="50" cy="50" r="45"/>
        <line class="cross-line cross-line1" x1="35" y1="35" x2="65" y2="65"/>
        <line class="cross-line cross-line2" x1="65" y1="35" x2="35" y2="65"/>
      </svg>
    </div>
    <h1>Application Auto Rejected</h1>
    <p>Important information regarding your application</p>
  </div>

  <!-- Body -->
  <div class="body">
    <p class="greeting">Dear {display_name},</p>
    <p class="sub">
      Your application is auto rejected by our system.
    </p>

    <!-- Application ID -->
    <div class="app-card">
      <div class="app-card-label">Your Application Number</div>
      <div class="app-card-value">{short_app_id}</div>
    </div>

  </div>

  <!-- Footer -->
  <div class="footer">
    <p>
      This is an automated message from <span class="brand">OnboardAI Banking</span>.<br/>
      Please do not reply to this email.
    </p>
  </div>

</div>
</body>
</html>"""

    plain = (
        f"Dear {display_name},\n\n"
        f"Your application is auto rejected by our system. We have detected high-risk activity beyond the permissible limit for your application.\n\n"
        f"Application No.: {application_id}\n\n"
        f"— OnboardAI Banking"
    )

    msg = EmailMessage()
    msg["From"]    = settings.SMTP_USER
    msg["To"]      = email
    msg["Subject"] = "Application Auto Rejected — OnboardAI Banking"
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
        logger.info("[EmailService] Auto rejection email sent to %s", email)
    except Exception as exc:
        logger.error("[EmailService] Failed to send auto rejection email: %s", exc)

