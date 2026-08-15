"""
VLM-assisted "teaching" suggestion for an unknown/non-standard P&ID symbol.

Given a crop of a symbol that didn't clearly match ISA-5.1/PIP (from
pid_expert_analyst, the CV pipeline's unknown_symbols queue, or a fresh
upload) plus whatever context is available, asks the configured VLM to
propose a classification — this is a SUGGESTION to pre-fill the human
review popup, never an auto-committed label. The actual write to
symbol_dictionary only happens once a human confirms via
POST /vlm/teach-symbol (see app/api/routes/vlm_extraction_db.py).
"""
import base64
from typing import Optional

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.schemas.teaching_schemas import TeachingSuggestion, TeachingSuggestionResult
from app.services.vlm.ollama_client import chat_with_image, extract_json, OllamaUnavailableError

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert P&ID (Piping and Instrumentation Diagram) analyst \
specializing in ISA-5.1 and PIP standards, helping a human teach the system \
about a symbol it could not confidently classify.

The following symbol did not match ISA-5.1 or PIP standards (or confidence \
was low). You are given a cropped image of the symbol plus whatever context \
is available. Propose your best classification for a human to review and \
confirm — never assert it as fact, and never invent details you can't \
support from the image and context given.

Output MUST be valid JSON only, in EXACTLY the structure given to you in \
the user message. No markdown, no code fences, no commentary."""


def _build_user_prompt(nearby_text: str, proposed_class: str, confidence: float) -> str:
    return f"""Nearby text: "{nearby_text}"
Current proposed class: "{proposed_class}"
Confidence: {confidence}

Please provide teaching information in this format:
{{
  "action": "teach",
  "new_class_name": "exact name to store (e.g. Special Ball Valve - Fire Safe)",
  "category": "instrument | equipment | piping_component | pipe_run | other",
  "standard_reference": "None / Custom / Company Standard",
  "description": "detailed description",
  "recommended_table": "instruments | equipment | piping_components | pipe_runs",
  "default_attributes": {{
    "key": "value"
  }},
  "isa_equivalent": "closest ISA-5.1 code if any",
  "pip_equivalent": "closest PIP symbol if any"
}}"""


def _image_to_b64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _call_anthropic(image_b64: str, user_prompt: str, model: str) -> str:
    import anthropic
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text", "text": user_prompt},
            ],
        }],
    )
    return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")


def _call_openai(image_b64: str, user_prompt: str, model: str) -> str:
    from openai import OpenAI
    settings = get_settings()
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ]},
        ],
        max_tokens=1024,
    )
    return response.choices[0].message.content or ""


def suggest_teaching(
    crop_image_path: str,
    nearby_text: str = "",
    proposed_class: str = "",
    confidence: float = 0.0,
    model: Optional[str] = None,
) -> TeachingSuggestionResult:
    """
    Asks the configured VLM to propose a TeachingSuggestion for one symbol
    crop. Never raises: if the provider is unreachable, disabled, or
    returns something unparseable after retries, returns a result with
    suggestion=None and `notes` explaining why — callers should fall back
    to an empty/manual popup in that case rather than blocking the human.
    """
    settings = get_settings()
    provider = settings.VLM_PROVIDER
    image_b64 = _image_to_b64(crop_image_path)
    user_prompt = _build_user_prompt(nearby_text, proposed_class, confidence)
    max_attempts = 1 + (settings.OLLAMA_JSON_RETRY_ATTEMPTS if provider == "ollama" else 1)

    used_model = None
    for attempt in range(1, max_attempts + 1):
        prompt_for_attempt = user_prompt
        if attempt > 1:
            prompt_for_attempt = (
                user_prompt
                + "\n\nYour previous response was not valid JSON matching this exact schema. "
                  "Return ONLY the raw JSON object this time — no markdown fences, no extra text."
            )

        try:
            if provider == "ollama":
                chat_result = chat_with_image(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt_for_attempt,
                    image_b64=image_b64,
                    model=model or settings.OLLAMA_VLM_MODEL,
                    force_json=True,
                    temperature=0.2,
                )
                raw_text = chat_result.raw_text
                used_model = chat_result.model
            elif provider == "anthropic":
                used_model = model or settings.VLM_MODEL
                raw_text = _call_anthropic(image_b64, prompt_for_attempt, used_model)
            elif provider == "openai":
                used_model = model or "gpt-4o"
                raw_text = _call_openai(image_b64, prompt_for_attempt, used_model)
            else:
                return TeachingSuggestionResult(
                    notes=f"No VLM provider configured (VLM_PROVIDER={provider}); no suggestion generated — fill the popup manually.",
                )
        except OllamaUnavailableError as exc:
            logger.error("ollama_teaching_suggestion_failed", extra={"context": {"error": str(exc), "attempt": attempt}})
            if attempt == max_attempts:
                return TeachingSuggestionResult(notes=f"Ollama unavailable: {exc}")
            continue

        parsed = extract_json(raw_text)
        if parsed is None:
            logger.warning("teaching_response_not_json", extra={"context": {
                "attempt": attempt, "raw_text_length": len(raw_text),
                "raw_text_start": raw_text[:300], "raw_text_end": raw_text[-300:],
            }})
            continue

        try:
            suggestion = TeachingSuggestion.model_validate(parsed)
            return TeachingSuggestionResult(suggestion=suggestion, model_used=used_model, parse_attempts=attempt)
        except ValidationError as exc:
            logger.warning("teaching_response_schema_mismatch", extra={"context": {
                "attempt": attempt, "error": str(exc)[:500], "raw_text_start": raw_text[:300],
            }})
            continue

    return TeachingSuggestionResult(
        parse_attempts=max_attempts,
        notes=f"Model returned no valid/parseable JSON after {max_attempts} attempt(s) — fill the popup manually.",
    )
