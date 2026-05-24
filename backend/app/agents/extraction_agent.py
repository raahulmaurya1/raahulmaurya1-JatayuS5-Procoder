import json
from loguru import logger
import google.generativeai as genai
from app.services.gemini_client import gemini_client
import re
import io
import os
#import pytesseract
from PIL import Image, ImageEnhance

# Configure Tesseract path for Windows
#pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

async def extract_document_data(file_bytes: bytes, mime_type: str) -> dict:
    """
    Sends the raw document bytes to Gemini Flash to extract structured JSON data.
    """
    prompt = """
    You are an expert OCR and document classification AI for a bank.
    Analyze the attached document and determine if it is a 'PAN', 'Aadhaar', or 'Signature'.
    
    Respond STRICTLY with a JSON object containing two top-level keys:
    1. 'document_type': EXACTLY one of ['PAN', 'Aadhaar', 'Signature', 'Unknown']
    2. 'extracted_fields': A nested JSON object capturing the relevant parameters.
    
    For PAN exclusively, extract:
    - "name"
    - "father_name"
    - "dob"
    - "id_number" (The alphanumeric PAN ID)
    
    For Aadhaar exclusively, extract:
    - "name"
    - "dob"
    - "address" (The full address string)
    - "id_number" (The 12-digit Aadhaar ID)
    
    For Signature, extract:
    - "detected": true or false
    
    If any field is missing or unreadable, set its value to null.
    Do NOT include markdown block formatting (like ```json), just return the raw JSON braces.
    """
    
    try:
        document_part = {
            "mime_type": mime_type,
            "data": file_bytes
        }
        
        response = await gemini_client.model.generate_content_async(
            [prompt, document_part],
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        
        extracted_json = json.loads(response.text)
        return extracted_json
        
    except Exception as e:
        logger.error(f"Failed to extract document data: {e}")
        return {"error": "Failed to extract data natively", "details": str(e)}

async def extract_document_data_from_text(text: str) -> dict:
    """
    Sends raw parsed text strings to Gemini Flash to extract structured JSON data.
    """
    prompt = f"""
    You are an expert OCR and document classification AI for a bank.
    Analyze the following document text and determine if it is a 'PAN', 'Aadhaar', or 'Signature'.
    
    Respond STRICTLY with a JSON object containing two top-level keys:
    1. 'document_type': EXACTLY one of ['PAN', 'Aadhaar', 'Signature', 'Unknown']
    2. 'extracted_fields': A nested JSON object capturing the relevant parameters.
    
    For PAN exclusively, extract:
    - "name"
    - "father_name"
    - "dob"
    - "id_number" (The alphanumeric PAN ID)
    
    For Aadhaar exclusively, extract:
    - "name"
    - "dob"
    - "address" (The full address string)
    - "id_number" (The 12-digit Aadhaar ID)
    
    For Signature, extract:
    - "detected": true or false
    
    If any field is missing or unreadable, set its value to null.
    Do NOT include markdown block formatting (like ```json), just return the raw JSON braces.
    
    Document Text:
    {text}
    """
    
    try:
        response = await gemini_client.model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        
        extracted_json = json.loads(response.text)
        return extracted_json
        
    except Exception as e:
        logger.error(f"Failed to extract document data from text: {e}")
        return {"error": "Failed to extract data from text natively", "details": str(e)}


# ─── SYNCHRONOUS VERSIONS FOR CELERY WORKERS ──────────────────────────────────
# Celery workers are synchronous. Using `generate_content_async` inside
# `asyncio.run()` causes 'Event loop is closed' errors due to gRPC internals.
# These sync versions use the blocking `generate_content()` call instead.

def extract_document_data_sync(file_bytes: bytes, mime_type: str) -> dict:
    """
    Synchronous version for use inside Celery background tasks.
    Sends raw document bytes to Gemini Vision to extract structured KYC data.
    """
    prompt = """
    You are an expert OCR and document classification AI for a bank.
    Analyze the attached document and determine if it is a 'PAN', 'Aadhaar', or 'Signature'.
    
    Respond STRICTLY with a JSON object containing two top-level keys:
    1. 'document_type': EXACTLY one of ['PAN', 'Aadhaar', 'Signature', 'Unknown']
    2. 'extracted_fields': A nested JSON object capturing the relevant parameters.
    
    For PAN exclusively, extract:
    - "name"
    - "father_name"
    - "dob"
    - "id_number" (The alphanumeric PAN ID)
    
    For Aadhaar exclusively, extract:
    - "name"
    - "dob"
    - "address" (The full address string)
    - "id_number" (The 12-digit Aadhaar ID)
    
    For Signature, extract:
    - "detected": true or false
    
    If any field is missing or unreadable, set its value to null.
    Do NOT include markdown block formatting (like ```json), just return the raw JSON braces.
    """
    try:
        document_part = {"mime_type": mime_type, "data": file_bytes}
        response = gemini_client.model.generate_content(
            [prompt, document_part],
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"[Sync] Failed to extract document data via Vision: {e}")
        return {"error": "Failed to extract data natively", "details": str(e)}


def extract_document_data_from_text_sync(text: str) -> dict:
    """
    Synchronous version for use inside Celery background tasks.
    Sends plain text to Gemini to extract structured KYC data.
    """
    prompt = f"""
    You are an expert OCR and document classification AI for a bank.
    Analyze the following document text and determine if it is a 'PAN', 'Aadhaar', or 'Signature'.
    
    Respond STRICTLY with a JSON object containing two top-level keys:
    1. 'document_type': EXACTLY one of ['PAN', 'Aadhaar', 'Signature', 'Unknown']
    2. 'extracted_fields': A nested JSON object capturing the relevant parameters.
    
    For PAN exclusively, extract:
    - "name"
    - "father_name"
    - "dob"
    - "id_number" (The alphanumeric PAN ID)
    
    For Aadhaar exclusively, extract:
    - "name"
    - "dob"
    - "address" (The full address string)
    - "id_number" (The 12-digit Aadhaar ID)
    
    If any field is missing or unreadable, set its value to null.
    Do NOT include markdown block formatting (like ```json), just return the raw JSON braces.
    
    Document Text:
    {text}
    """
    try:
        response = gemini_client.model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"[Sync] Failed to extract document data from text: {e}")
        return {"error": "Failed to extract data from text natively", "details": str(e)}

def extract_and_classify_local(image_bytes: bytes) -> dict:
    """
    Tier 0: Strictly local extraction using Tesseract OCR and PIL Preprocessing.
    Enhanced to extract DOB, IDs, Name, Father's Name, and cleaned Address.
    """
    try:
        # 1. Load the image with PIL
        img = Image.open(io.BytesIO(image_bytes))
        
        # 2. Preprocessing: Convert to Grayscale
        img = img.convert('L')
        
        # 3. Preprocessing: Boost Contrast (2.0x)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)
        
        # 4. Extract text via Tesseract
      #  raw_text = pytesseract.image_to_string(img)
        raw_text = raw_text.strip()
        logger.info(f"[OnboardAI][TESSERACT_OCR] Raw text length: {len(raw_text)}")
        
        # 5. Advanced Field Extraction
        # DOB: Strict format-bound
        dob_match = re.search(r'\b(\d{2}[/-]\d{2}[/-]\d{4})\b', raw_text)
        # PAN ID
        pan_match = re.search(r'[A-Z]{5}[0-9]{4}[A-Z]{1}', raw_text.upper())
        # Aadhaar ID
        aadhaar_match = re.search(r'\b\d{4}\s?\d{4}\s?\d{4}\b', raw_text)
        # Address (Aadhaar): Strictly look for "Address:" (with colon) followed by spaces/newlines
        address_match = re.search(r'\bAddress:[\s\n]+(.*?\d{6})', raw_text, re.DOTALL)
        
        dob = dob_match.group(1).strip() if dob_match else None
        pan_id = pan_match.group() if pan_match else None
        aadhar_id = re.sub(r'\s+', '', aadhaar_match.group()) if aadhaar_match else None
        address = address_match.group(1).strip() if address_match else None
        
        # 6. Name & Father's Name Extraction (Logic: Targeted Regex with no newline bleed)
        name_regex = r'(?i)(?:Name|नाम|ava /Name)[\s:]*\n?([A-Z ]{3,})'
        father_regex = r'(?i)(?:Father\'s Name|पिता का नाम)[\s:]*\n?([A-Z ]{3,})'
        
        name_match = re.search(name_regex, raw_text)
        father_match = re.search(father_regex, raw_text)
        
        name = name_match.group(1).strip() if name_match else None
        father_name = father_match.group(1).strip() if father_match else None
        
        # 7. Address Cleanup
        if address:
            # Removes "C/O: [Name]," cleanly by stopping at the first comma
            address = re.sub(r'^(?:C/O|S/O|W/O|D/O)[\s:]*[^,]+,\s*', '', address, flags=re.IGNORECASE).strip()
            # Clean up random newlines inside the address
            address = address.replace('\n', ' ')

        # 8. Document Classification
        doc_type = "UNKNOWN"
        if pan_id:
            doc_type = "PAN"
        elif aadhar_id:
            doc_type = "AADHAAR"
            
        # 9. Return standard structured format
        return {
            "document_type": doc_type,
            "aadhar_id": aadhar_id,
            "pan_id": pan_id,
            "name": name,
            "father_name": father_name,
            "dob": dob,
            "address": address,
            "raw_text": raw_text,
            "extracted_fields": {
                "aadhar_id": aadhar_id,
                "pan_id": pan_id,
                "name": name,
                "father_name": father_name,
                "dob": dob,
                "address": address
            }
        }
    except Exception as e:
        logger.error(f"[OnboardAI][TESSERACT_OCR] Critical failure: {e}")
        return {
            "document_type": "UNKNOWN",
            "error": str(e),
            "aadhar_id": None,
            "pan_id": None,
            "name": None,
            "father_name": None,
            "dob": None,
            "address": None
        }


# ─── GST CERTIFICATE PARSER ────────────────────────────────────────────────────

def _clean_str(s: str) -> str:
    """Replaces newlines/carriage-returns with spaces and strips whitespace."""
    if not s:
        return ""
    return s.replace('\n', ' ').replace('\r', ' ').strip()


def _fmt_date(dmy: str) -> str | None:
    """Converts DD/MM/YYYY -> YYYY-MM-DD for PostgreSQL DATE columns."""
    if not dmy:
        return None
    parts = dmy.strip().split('/')
    if len(parts) == 3:
        dd, mm, yyyy = parts
        if len(yyyy) == 4 and yyyy.isdigit():
            return f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"
    return None


def extract_gst_data(raw_text: str) -> dict | None:
    """
    Parses raw OCR text from a GST Registration Certificate.

    Strategy
    --------
    1. Extract GSTIN & dates from the *raw* text (slashes are intact).
    2. **Flatten** the entire string: strip quotes / commas, collapse
       whitespace into a single space.
    3. Use a **multi-strategy** approach to pull Legal Name, Trade Name,
       Constitution, and Address from the flat string.

       * Strategy A  - direct "label ... value ... next-label" capture.
       * Strategy B  - skip interleaved labels, then split values by
         row-numbers (handles column-first OCR).
    """
    try:
        logger.info("[OnboardAI][GST_PARSER] ── RAW OCR TEXT ──\n%s", raw_text[:2000])

        # ── Step 1: Extract GSTIN from RAW text (before flattening) ──
        gstin = None
        m = re.search(r'(?i)Registration\s*Number[\s:]*([A-Z0-9]{15})', raw_text)
        if not m:
            # Fallback: CSV-format OCR strips colons; try without colon requirement
            m = re.search(r'(?i)Registration\s+Number\s+([A-Z0-9]{15})', raw_text)
        if m:
            gstin = m.group(1).upper().strip()

        # ── Step 2: Extract dates from RAW text (slashes survive here) ──
        date_of_liability = None
        m = re.search(r'(?i)Date\s*of\s*Liability[\s\S]*?(\d{2}[/-]\d{2}[/-]\d{4})', raw_text)
        if m:
            date_of_liability = _fmt_date(m.group(1).replace('-', '/'))

        from_date = to_date = None
        m = re.search(
            r'(?i)From\s*(?:\d+\.)?\s*(\d{2}[/-]\d{2}[/-]\d{4})'
            r'[\s\S]*?'
            r'To\s*(?:\d+\.)?\s*(\d{2}[/-]\d{2}[/-]\d{4})',
            raw_text
        )
        if m:
            from_date = _fmt_date(m.group(1).replace('-', '/'))
            to_date = _fmt_date(m.group(2).replace('-', '/'))
        else:
            validity_block = re.search(r'(?i)Period\s*of\s*Validity(.{0,300})', raw_text, re.DOTALL)
            if validity_block:
                dates_found = re.findall(r'(\d{2}[/-]\d{2}[/-]\d{4})', validity_block.group(1))
                if dates_found:
                    from_date = _fmt_date(dates_found[0].replace('-', '/'))
                if len(dates_found) >= 2:
                    to_date = _fmt_date(dates_found[1].replace('-', '/'))

        # ── Step 3: Flatten and sanitize ──
        flat = re.sub(r'["\',]', ' ', raw_text)
        flat = re.sub(r'\s+', ' ', flat).strip()
        logger.info("[OnboardAI][GST_PARSER] ── FLAT TEXT ──\n%s", flat[:2000])

        # Fallback: try GSTIN on flattened text (handles CSV-format OCR)
        if not gstin:
            m = re.search(r'(?i)Registration\s+Number\s+([A-Z0-9]{15})', flat)
            if m:
                gstin = m.group(1).upper().strip()

        # Fallback: try dates on flattened text
        if not from_date:
            m = re.search(
                r'(?i)From\s+(\d{2}[/-]\d{2}[/-]\d{4})\s+To\s+(\d{2}[/-]\d{2}[/-]\d{4})',
                flat
            )
            if m:
                from_date = _fmt_date(m.group(1).replace('-', '/'))
                to_date = _fmt_date(m.group(2).replace('-', '/'))

        # ── Step 4: Multi-strategy field extraction ──

        def _simple_extract(label_re, stop_re):
            """Strategy A: label ... (value) ... stop-anchor"""
            pat = label_re + r'[\s:]*(.*?)(?=\s*' + stop_re + r'|\s*$)'
            m = re.search(pat, flat, re.IGNORECASE)
            if m:
                val = m.group(1).strip()
                # Strip trailing row numbers like " 2." or " 3."
                val = re.sub(r'\s+\d+\.\s*$', '', val).strip()
                # Reject if it's just a stray number or extremely short label
                if not val or re.match(r'^\d+\.?$', val):
                    return None
                return val
            return None

        # ─── Legal Name ───
        legal_name = _simple_extract(
            r'(?:1\s*\.\s*)?Legal\s+Name',
            r'(?:Trade\s+Name|Constitution\s+of\s+Business|Address|Date\s+of\s+Liability|\d+\s*\.\s*(?:Trade|Constitution|Address))'
        )

        # ─── Trade Name ───
        trade_name = _simple_extract(
            r'(?:2\s*\.\s*)?Trade\s+Name[\s,]*(?:if\s+any)?',
            r'(?:Constitution\s+of\s+Business|Address|Date\s+of\s+Liability|\d+\s*\.\s*(?:Constitution|Address))'
        )

        # ─── Constitution ───
        constitution = _simple_extract(
            r'(?:3\s*\.\s*)?Constitution\s+of\s+Business',
            r'(?:Address|Date\s+of\s+Liability|\d+\s*\.\s*(?:Address|Date))'
        )

        # ─── Address ───
        address = _simple_extract(
            r'(?:4\s*\.\s*)?Address\s+of\s+Principal\s+Place\s+of\s+Business',
            r'(?:Date\s+of\s+Liability|Period\s+of\s+Validity|Type\s+of\s+Registration|Particulars|Details|\d+\s*\.\s*(?:Date|Period|Type))'
        )
        if not address:
            address = _simple_extract(
                r'(?:4\s*\.\s*)?Address',
                r'(?:Date\s+of\s+Liability|Period\s+of\s+Validity|Type\s+of\s+Registration|\d+\s*\.\s*(?:Date|Period|Type))'
            )

        # ── Strategy B fallback: interleaved columns ──
        # OCR sometimes groups all labels together then all values:
        #   "Legal Name Trade Name if any BHARAT ... 2. BHARAT ... 3. Partnership ..."
        if not legal_name:
            m = re.search(
                r'Legal\s+Name\s+'
                r'(?:Trade\s+Name[\s,]*(?:if\s+any)?\s*)?'
                r'(?:Constitution\s+of\s+Business\s*)?'
                r'(?:Address\s+of\s+Principal\s+Place\s+of\s+Business\s*)?'
                r'(.+?)(?=\s*(?:Constitution|Address|Date|Period|\d+\s*\.\s*(?:Constitution|Address|Date))\s|\s*$)',
                flat, re.IGNORECASE
            )
            if m:
                val = m.group(1).strip()
                val = re.split(r'\s+\d+\.\s*', val)[0].strip()
                legal_name = val if val and not re.match(r'^(?:\d+\.\s*)+$', val + ' ') else None

        if not trade_name:
            m = re.search(
                r'Trade\s+Name[\s,]*(?:if\s+any)?\s+'
                r'(?:Constitution\s+of\s+Business\s*)?'
                r'(?:Address\s+of\s+Principal\s+Place\s+of\s+Business\s*)?'
                r'(.+?)(?=\s*(?:Constitution|Address|Date|Period|\d+\s*\.\s*(?:Constitution|Address|Date))\s|\s*$)',
                flat, re.IGNORECASE
            )
            if m:
                val = m.group(1).strip()
                parts = re.split(r'\s+\d+\.\s*', val)
                trade_name = parts[1].strip() if len(parts) > 1 else parts[0].strip()
                trade_name = trade_name if trade_name and not re.match(r'^(?:\d+\.\s*)+$', trade_name + ' ') else None

        if not constitution:
            m = re.search(
                r'Constitution\s+of\s+Business\s+'
                r'(?:Address\s+of\s+Principal\s+Place\s+of\s+Business\s*)?'
                r'(.+?)(?=\s*(?:Address|Date|Period|\d+\s*\.\s*(?:Address|Date))\s|\s*$)',
                flat, re.IGNORECASE
            )
            if m:
                val = m.group(1).strip()
                val = re.split(r'\s+\d+\.\s*', val)[0].strip()
                constitution = val if val and not re.match(r'^(?:\d+\.\s*)+$', val + ' ') else None

        if not address:
            m = re.search(
                r'Address\s+of\s+Principal\s+Place\s+of\s+Business\s+'
                r'(.+?)(?=\s*(?:Date\s+of\s+Liability|Period\s+of\s+Validity|\d+\s*\.\s*(?:Date|Period))\s|\s*$)',
                flat, re.IGNORECASE
            )
            if m:
                val = m.group(1).strip()
                val = re.sub(r'\s+\d+\.\s*$', '', val).strip()
                address = val if val and not re.match(r'^(?:\d+\.\s*)+$', val + ' ') else None

        # Clean trade name defaults
        if trade_name and trade_name.lower() in ('na', 'n/a', 'none', '-'):
            trade_name = None

        # Reject obvious garbage (lone numbers, label fragments) to allow Strategy C to run
        def is_garbage(v):
            if not v: return True
            v = v.strip()
            if re.match(r'^(?:\d+\.\s*)+$', v + ' '): return True
            if re.search(r'(?:Principal\s+Place\s+of\s+Business|Date\s+of\s+Liability|Period\s+of\s+Validity)', v, re.IGNORECASE): return True
            return False

        if is_garbage(legal_name): legal_name = None
        if is_garbage(trade_name): trade_name = None
        if is_garbage(constitution): constitution = None
        if is_garbage(address): address = None

        # ── Strategy C fallback: Block format ──
        # Tesseract sometimes groups all labels into one block, and all values into another.
        if not legal_name and not address:
            constitution_re = r'(Partnership|Proprietorship|Private\s+Limited\s+Company|Public\s+Limited\s+Company|Society|Trust|Hindu\s+Undivided\s+Family|Limited\s+Liability\s+Partnership)'
            date_re = r'(\d{2}[/-]\d{2}[/-]\d{4})'
            m = re.search(r'(?i)(.*?)\s+' + constitution_re + r'\s+(.*?)\s+' + date_re, flat)
            if m:
                names_part = m.group(1).strip()
                const_val = m.group(2).strip()
                addr_val = m.group(3).strip()
                
                labels_re = r'(?i)(?:Registration\s+Number\s+[A-Z0-9]+\s*)?(?:\d+\.\s*)?Legal\s+Name\s+(?:\d+\.\s*)?(?:Trade\s+Name[\s,]*(?:if\s+any)?\s*)?(?:\d+\.\s*)?(?:Constitution\s+of\s+Business\s*)?(?:\d+\.\s*)?(?:Address\s+of\s+Principal\s+Place\s+of\s+Business\s*)?(?:\d+\.\s*)?(?:Date\s+of\s+Liability\s*)?(?:\d+\.\s*)?(?:Period\s+of\s+Validity\s*)?(?:\d+\.\s*)?(?:Type\s+of\s+Registration\s*)?(?:\d+\.\s*)?(?:From\s*)?(?:To\s*)?'
                # Strip all text up to the last known label
                last_label_m = re.search(
                    r'.*(?:Type\s+of\s+Registration|Date\s+of\s+Liability|Principal\s+Place\s+of\s+Business|Trade\s+Name(?:[\s,]*(?:if\s+any)?)?)[\s\d\.]*(.*)$', 
                    names_part, 
                    re.IGNORECASE
                )
                if last_label_m:
                    clean_names = last_label_m.group(1).strip()
                else:
                    clean_names = re.sub(labels_re, ' ', names_part).strip()
                
                clean_names = re.sub(r'\s+', ' ', clean_names).strip()
                
                if clean_names:
                    legal_name = clean_names
                    words = clean_names.split()
                    if len(words) % 2 == 0 and len(words) > 0:
                        half = len(words) // 2
                        first_half = " ".join(words[:half])
                        second_half = " ".join(words[half:])
                        if first_half == second_half:
                            legal_name = first_half
                            trade_name = second_half
                        elif " " in clean_names:
                            # If not perfect duplicate, sometimes it's two long names next to each other
                            # But without a separator, it's hard. Let's just fallback to the first half if it's very long
                            if len(words) > 4:
                                pass # keep as is, or we could apply NLP

                constitution = const_val
                address = re.sub(labels_re, ' ', addr_val).strip()
                address = re.sub(r'\s+\d+\.\s+', ' ', address).strip()

        # Clean stray leading row-numbers from all fields
        if legal_name:
            legal_name = re.sub(r'^\d+\.\s*', '', legal_name).strip()
        if trade_name:
            trade_name = re.sub(r'^\d+\.\s*', '', trade_name).strip()
        if constitution:
            constitution = re.sub(r'^\d+\.\s*', '', constitution).strip()
        if address:
            address = re.sub(r'^\d+\.\s*', '', address).strip()
            address = re.sub(r'\s+\d+\.\s+', ' ', address).strip()

        # ── Step 5: Require at least GSTIN or Legal Name ──
        if not gstin and not legal_name:
            logger.warning("[OnboardAI][GST_PARSER] Could not extract GSTIN or Legal Name. Returning None.")
            return None

        result = {
            "document_type": "GST",
            "gstin": gstin,
            "legal_name": legal_name,
            "trade_name": trade_name,
            "constitution": constitution,
            "address": address,
            "date_of_liability": date_of_liability,
            "validity_from": from_date,
            "validity_to": to_date,
        }
        logger.info(f"[OnboardAI][GST_PARSER] Extracted: {result}")
        return result

    except Exception as e:
        logger.exception(f"[OnboardAI][GST_PARSER] Failed to parse GST data: {e}")
        return None
