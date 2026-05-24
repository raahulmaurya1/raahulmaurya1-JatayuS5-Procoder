import json
import re
import asyncio
from enum import Enum
from pydantic import BaseModel, Field
import google.generativeai as genai

from app.services.gemini_client import gemini_client
from loguru import logger

class IntentCategory(str, Enum):
    RETAIL_SAVINGS = "retail_savings"
    DIGITAL_ONLY   = "digital_only"
    SME_CURRENT    = "sme_current"
    RE_KYC         = "re_kyc"
    REACTIVATION   = "reactivation"
    UNKNOWN        = "unknown"

# Lifecycle intents — these trigger the LifecycleOrchestrator strategy
LIFECYCLE_INTENTS = {IntentCategory.RE_KYC, IntentCategory.REACTIVATION}

class IntentClassificationResult(BaseModel):
    intent: IntentCategory = Field(description="The determined category of onboarding scenario.")
    confidence: float = Field(description="The AI's confidence in this classification from 0 to 1.")
    reasoning: str = Field(description="Short rationale for why this classification was chosen.")


async def classify_intent(user_input: str) -> IntentClassificationResult:
    """
    Passes the natural language user input to Gemini to determine
    the correct onboarding/lifecycle intent.
    """
    
    # ── LATENCY OPTIMIZATION: Keyword Heuristics ──
    user_input_lower = user_input.lower()
    if "re-kyc" in user_input_lower or "re kyc" in user_input_lower or "rekyc" in user_input_lower or "update kyc" in user_input_lower or "update my kyc" in user_input_lower:
        return IntentClassificationResult(intent=IntentCategory.RE_KYC, confidence=0.9, reasoning="Keyword match")
    elif "reactivat" in user_input_lower or "unfreeze" in user_input_lower or "dormant" in user_input_lower:
        return IntentClassificationResult(intent=IntentCategory.REACTIVATION, confidence=0.9, reasoning="Keyword match")
    elif "savings" in user_input_lower or "retail" in user_input_lower:
        return IntentClassificationResult(intent=IntentCategory.RETAIL_SAVINGS, confidence=0.9, reasoning="Keyword match")
    elif "digital" in user_input_lower or "zero balance" in user_input_lower:
        return IntentClassificationResult(intent=IntentCategory.DIGITAL_ONLY, confidence=0.9, reasoning="Keyword match")
    elif "sme" in user_input_lower or "business" in user_input_lower or "corporate" in user_input_lower:
        return IntentClassificationResult(intent=IntentCategory.SME_CURRENT, confidence=0.9, reasoning="Keyword match")
    elif any(kw in user_input_lower for kw in (
        "new account", "open account", "open new account",
        "open bank account", "bank account open", "new bank account",
        "open a account", "open an account", "create account",
        "want to open", "want account", "need account", "need a account",
        "open a bank", "start account",
    )):
        # Generic "open account" detected — user clearly wants to onboard
        # but hasn't specified the account type. Return RETAIL_SAVINGS immediately
        # so the state evaluator re-prompts for the specific type, instead
        # of wasting 3 LLM retries (~20s) that return RETAIL_SAVINGS anyway.
        return IntentClassificationResult(
            intent=IntentCategory.RETAIL_SAVINGS, #default case to retail account
            confidence=0.8,
            reasoning="Generic account opening detected — account type unspecified"
        )
    
    prompt = f"""
    You are an AI core agent for a bank account management system. Your task is to classify a user's
    natural language request into one of six specific categories.
    
    CRITICAL RULE: If the user wants to open an account but does not 
    explicitly specify 'Retail', 'SME', 'Business', or 'Digital',if the user hasn't specified a clear intent (e.g. just 'open account'), you MUST return 'retail_savings'.
    
    Categories:
    1. 'retail_savings': Standard personal savings accounts for individuals.
       (e.g., "I want to open a retail savings account", "start opening an account", "I need a personal savings account")
    2. 'digital_only': Zero-balance, paperless, instant digital accounts.
       (e.g., "I want a quick digital account", "zero balance account", "instant online account")
    3. 'sme_current': Business current accounts for Small and Medium Enterprises.
       (e.g., "I want to open a business account", "SME account", "corporate account for my firm")
    4. 're_kyc': An EXISTING customer who needs to update or re-verify their KYC documents.
       (e.g., "I need to update my KYC", "bank asked me to re-verify", "update my documents")
    5. 'reactivation': An EXISTING customer whose account is dormant or suspended and needs reactivating.
       (e.g., "reactivate my account", "my account was deactivated", "unfreeze my bank account")
    6. 'unknown': when the input is a greeting/unclear.

    CRITICAL: If the user sends a greeting (e.g. "hello", "hi"), chit-chat, or any ambiguous/conversational 
    request that does not map to a specific account type or lifecycle action, you MUST output valid JSON: 
    {{"intent": "unknown", "confidence": 1.0, "reasoning": "Conversational or ambiguous input"}}.

    User Query: '{user_input}'
    
    Respond STRICTLY in JSON format with the following keys:
    - intent: (string, exactly one of "retail_savings", "digital_only", "sme_current", "re_kyc", "reactivation", "unknown")
    - confidence: (float, 0.0 to 1.0)
    - reasoning: (string, brief explanation of your choice)
    """
    
    logger.info(f"[OnboardAI][INTENT] Classifying input: {user_input[:100]}...")
    
    # ── SINGLE-SHOT LLM CALL WITH BULLETPROOF EXTRACTION ──
    # Retries are ONLY for transient API errors (network/500), NOT for
    # JSON parsing failures. If the LLM returns plain text, we fail-fast.
    try:
        raw_response = await gemini_client.model.generate_content_async(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
            )
        )

        raw_text = raw_response.text.strip()

        # ── BULLETPROOF EXTRACTION: regex out the JSON object ──
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)

        if not json_match:
            # The LLM returned pure text, no JSON. DO NOT RETRY. Fast-fail.
            logger.warning(f"[OnboardAI][INTENT] LLM returned non-JSON text: {raw_text[:120]!r} — fast-failing to UNKNOWN")
            return IntentClassificationResult(
                intent=IntentCategory.UNKNOWN,
                confidence=0.0,
                reasoning=f"LLM returned non-JSON response"
            )

        data = json.loads(json_match.group(0))
        intent_val = data.get("intent", "unknown")

        # Validate against enum
        try:
            intent_category = IntentCategory(intent_val)
        except ValueError:
            intent_category = IntentCategory.UNKNOWN

        logger.info(f"[OnboardAI][INTENT] LLM Result: {intent_category} (conf: {data.get('confidence', 0.0)})")

        return IntentClassificationResult(
            intent=intent_category,
            confidence=data.get("confidence", 0.0),
            reasoning=data.get("reasoning", "No context provided")
        )

    except Exception as e:
        # Transient API error (network, 500, etc.) — fail-fast, no retry.
        logger.error(f"[OnboardAI][INTENT] LLM Classification failed: {e}")
        return IntentClassificationResult(
            intent=IntentCategory.UNKNOWN,
            confidence=0.0,
            reasoning=f"Fallback due to model error: {str(e)}"
        )
