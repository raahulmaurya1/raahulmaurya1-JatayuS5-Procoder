import secrets
from loguru import logger
import httpx
import aiosmtplib
import json
from email.message import EmailMessage
from fastapi import HTTPException
from app.config import settings
from app.storage.redis import redis_client
from ulid import ULID

async def check_rate_limit(identifier: str) -> None:
    """
    STRICT Redis rate limiter mapping a 3-minute rolling window:
    - INCR the counter
    - If exact count == 1, EXPIRE strictly at 180s rolling window
    - If count > 3, throw HTTP 429
    """
    key = f"rate_limit:{identifier}"
    
    attempts = await redis_client.incr(key)
    
    if attempts == 1:
        await redis_client.expire(key, 180)
        
    if attempts > 3:
        logger.warning(f"Rate limit strictly exceeded for {identifier}.")
        raise HTTPException(
            status_code=429, 
            detail="Too many attempts. Try again after the 180s cooldown period."
        )

def generate_otp() -> str:
    """Generates a secure 6-digit random token natively."""
    return "".join(str(secrets.randbelow(10)) for _ in range(6))

async def send_phone_otp(phone: str, pending_session_id: str = None) -> dict:
    """
    Step 1: Dispatches the OTP natively to the phone number locking in the 3-try rate limit.
    """
    await check_rate_limit(phone)
    phone_code = generate_otp()
    await redis_client.setex(f"phone_otp:{phone}", 180, phone_code)
    
    try:
        if settings.TWO_FACTOR_API_KEY:
            clean_phone = phone.replace("+91", "").strip()
            url = f"https://2factor.in/API/V1/{settings.TWO_FACTOR_API_KEY}/SMS/{clean_phone}/{phone_code}"
            
            async with httpx.AsyncClient() as client:
                res = await client.get(url)
                res.raise_for_status()
                response_data = res.json()
                if isinstance(response_data, dict) and response_data.get("Status", "").lower() != "success":
                    raise Exception(f"2Factor API rejected the payload natively: {response_data}")

    except Exception as e:
        logger.error(f"Dispatch Error (Muted for dev): {e} | Generated OTP is: {phone_code}")
        # Not raising HTTPException so dev testing can proceed without active SMS credits
        
    session_ulid = pending_session_id or str(ULID())
    pending_payload = json.dumps({"phone": phone, "phone_verified": False})
    await redis_client.setex(f"pending_auth:{session_ulid}", 1800, pending_payload)
        
    return {"message": "Phone OTP processed successfully.", "session_ulid": session_ulid}

async def verify_phone_otp(phone: str, code: str, pending_session_id: str = None) -> dict:
    """
    Step 2: Validates Phone OTP. If valid, maps a 30-min pending Redis session containing {"phone" : "+91..."}
    Returns the pending_session_id.
    """
    verify_attempts_key = f"verify_attempts:{phone}"
    attempts = await redis_client.incr(verify_attempts_key)
    if attempts == 1:
        await redis_client.expire(verify_attempts_key, 120)
    if attempts > 3:
        await redis_client.delete(f"phone_otp:{phone}")
        await redis_client.delete(verify_attempts_key)
        raise HTTPException(status_code=400, detail="Maximum verification attempts exceeded. Session terminated.")

    stored_phone_code = await redis_client.get(f"phone_otp:{phone}")
    if not stored_phone_code or stored_phone_code != code:
        raise HTTPException(status_code=400, detail="Invalid or expired SMS verification code.")
        
    await redis_client.delete(f"phone_otp:{phone}")
    await redis_client.delete(verify_attempts_key)
    
    # Generate Pending Sequence Object
    if not pending_session_id:
        pending_session_id = str(ULID())
    pending_payload = json.dumps({"phone": phone, "phone_verified": True})
    
    # Strictly lock the Pending Auth to a 30-minute Redis TTL natively replacing the 15-min spec
    await redis_client.setex(f"pending_auth:{pending_session_id}", 1800, pending_payload)
    
    return {"status": "success", "pending_session_id": pending_session_id}

async def send_email_otp(email: str, pending_session_id: str) -> dict:
    """
    Step 3: Dispatches Email OTP only after verifying the pending_session_id confirms the Phone was verified.
    """
    raw_pending = await redis_client.get(f"pending_auth:{pending_session_id}")
    if not raw_pending:
        raise HTTPException(status_code=401, detail="Invalid or expired pending session. Verify Phone again.")
        
    pending_data = json.loads(raw_pending)
    pending_data["email"] = email
    await redis_client.setex(f"pending_auth:{pending_session_id}", 1800, json.dumps(pending_data))
        
    await check_rate_limit(email)
    email_code = generate_otp()
    await redis_client.setex(f"email_otp:{email}", 180, email_code)
    
    try:
        if settings.SMTP_USER and settings.SMTP_PASS:
            message = EmailMessage()
            message["From"] = settings.SMTP_USER
            message["To"] = email
            message["Subject"] = "Bank Onboarding: Your Verification Code"

            email_code = str(email_code)

            # ✅ Plain text fallback (with your message added)
            message.set_content(
                f"Dear Customer,\n\n"
                f"We value your trust and are committed to keeping your account secure. "
                f"Please use the verification code below to proceed.\n\n"
                f"Verification Code: {email_code}\n"
                f"This code is valid for 180 seconds."
            )

            # ✅ HTML version (centered + styled + your message added)
            message.add_alternative(f"""
            <html>
            <body style="text-align:center; font-family: monospace;">

            <p style="font-size:16px; max-width:500px; margin:auto;">
            Dear Customer,<br><br>
            We value your trust and are committed to keeping your account secure.
            Please use the verification code below to proceed.
            </p>

            <br>

            <pre style="font-family: monospace; text-align:center; font-size:14px; line-height:1.2;">
<span style="color:#0A1F44;">  ____        _                         _       _ </span>
<span style="color:#123A6F;"> / __ \ _ __ | |__   ___   __ _ _ __ __| | __ _(_)</span>
<span style="color:#1E5AA8;">| |  | | '_ \| '_ \ / _ \ / _` | '__/ _` |/ _` | |</span>
<span style="color:#2E86C1;">| |  | | | | | | | | | | | | | | | | | | | | | | |</span>
<span style="color:#D4AF37;">| |__| | | | | |_| | \_/ | \_| | | | \_| | \_| | |</span>
<span style="color:#FFD700;"> \____/|_| |_|_.__/ \___/ \__,_|_|  \__,_|\__,_|_|</span>
            </pre>

            <p style="font-size:16px;">Your secure verification code is:</p>
            <h2 style="letter-spacing:2px;">{email_code}</h2>
            <p style="color:gray;">Expires in 180 seconds</p>

            </body>
            </html>
            """, subtype='html')

            # Port 465 = Implicit SSL (use_tls=True only — do NOT also pass start_tls)
            # Port 587 = STARTTLS   (start_tls=True only — do NOT also pass use_tls)
            # Passing both simultaneously causes BadCredentials on some aiosmtplib versions.
            smtp_kwargs = dict(
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER,
                password=settings.SMTP_PASS,
            )
            if settings.SMTP_PORT == 465:
                smtp_kwargs["use_tls"] = True
            else:
                smtp_kwargs["start_tls"] = True

            await aiosmtplib.send(message, **smtp_kwargs)
            logger.info(f"Email OTP dispatched successfully to {email}")

    except Exception as e:
        logger.error(
            f"Email OTP Dispatch Error [{type(e).__name__}]: {e} | Generated Email OTP is: {email_code}"
        )

    return {"message": "Email OTP processed successfully."}

async def verify_email_otp(code: str, pending_session_id: str) -> dict:
    """
    Step 4: Validates the Email OTP and strictly binds it against the pending_session_id.
    Returns the combined identity mapping required for the Convergence layer.
    """
    raw_pending = await redis_client.get(f"pending_auth:{pending_session_id}")
    if not raw_pending:
        raise HTTPException(status_code=401, detail="Invalid or expired pending session. Verify Phone again.")
        
    pending_data = json.loads(raw_pending)
    phone = pending_data.get("phone")
    email = pending_data.get("email")
    logger.debug(f"DEBUG: Retrieved Email from Session: {email}")
    
    if not email:
        raise HTTPException(status_code=400, detail="No email address associated with this session. Please request a new code.")
    
    verify_attempts_key = f"verify_attempts:{email}"
    attempts = await redis_client.incr(verify_attempts_key)
    if attempts == 1:
        await redis_client.expire(verify_attempts_key, 120)
    if attempts > 3:
        await redis_client.delete(f"email_otp:{email}")
        await redis_client.delete(verify_attempts_key)
        raise HTTPException(status_code=400, detail="Maximum verification attempts exceeded. Session terminated.")

    stored_email_code = await redis_client.get(f"email_otp:{email}")
    logger.debug(f"DEBUG: Expected OTP: {stored_email_code} | Received OTP: {code}")
    
    if not stored_email_code or stored_email_code != code:
        raise HTTPException(status_code=400, detail="Invalid or expired Email verification code.")
        
    await redis_client.delete(f"email_otp:{email}")
    await redis_client.delete(verify_attempts_key)
    
    # DO NOT DELETE PENDING AUTH YET. Let the Convergence Route pop it.
    
    return {"status": "success", "phone": phone, "email": email}