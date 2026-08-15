"""
"Expert P&ID analyst" feature.

Unlike text_extractor.vlm_extract_text() (a narrow OCR top-up for a single
low-confidence crop), this module sends a full page image — or a specific
region of one — to a vision-language model with a strict ISA-5.1/PIP system
prompt and asks it to extract structured engineering data into four tables:
instruments, equipment, pipe_runs, and piping_components.

Runs entirely against a local Ollama server by default (VLM_PROVIDER=ollama)
— no API key required, no data leaves the host running Ollama. If
VLM_PROVIDER is "anthropic" or "openai" instead, the same prompt/schema is
sent to those providers so this feature works with any configured VLM
backend, not just Ollama.
"""
import base64
import io
from typing import Optional

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.schemas.vlm_schemas import PidExtractionResult
from app.services.vlm.ollama_client import chat_with_image, extract_json, OllamaUnavailableError

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert P&ID (Piping and Instrumentation Diagram) analyst \
specializing in ISA-5.1 and PIP standards.

Your task is to analyze the provided P&ID image (a full page or a specific \
region of one) and extract structured engineering data from every symbol \
and piece of text you can identify.

STRICT RULES:
1. All instrument tags and identification must follow the ISA-5.1 standard \
(function letters such as PT, FT, LT, TT, PIC, FIC, XV, HV, PV, etc.).
2. All piping symbols, pipe runs, and piping components must follow PIP \
standards (and ASME/ANSI where applicable).
3. Never guess. If a field's value is not clearly legible or not present \
on the drawing, leave it null (or an empty string for text fields) rather \
than inventing a tag, size, spec, rating, or any other value.
4. Extract data into EXACTLY the five arrays and field names given to you \
in the user message — do not add, rename, or remove keys, and do not merge \
or split entries beyond what is actually drawn.
5. If you see a symbol or icon that is clearly part of the P&ID but you \
cannot confidently classify it as a standard ISA-5.1 instrument, PIP \
equipment/piping symbol, or is otherwise non-standard/illegible, do NOT \
force it into one of the four tables and do NOT skip it silently — report \
it in "unknown_symbols" instead, with as much of a description and nearby \
text as you can make out.
6. Output MUST be valid JSON only. No markdown, no code fences, no \
commentary before or after the JSON.
"""


def _build_user_prompt(image_width: int, image_height: int, page_number: int) -> str:
    return f"""The image you are given is page {page_number}, {image_width}x{image_height} \
pixels. Report every "bbox" as [x1, y1, x2, y2] in those pixel coordinates \
(top-left origin, x right, y down). Set every "page_number" field to \
{page_number}.

Return ONLY a single JSON object with exactly this shape:

{{
  "instruments": [
    {{
      "instrument_tag": "e.g. PT-101A",
      "instrument_type": "e.g. Pressure Transmitter",
      "identification": "ISA-5.1 letters + function (e.g. PT = Pressure Transmitter)",
      "location": "local / panel / shared",
      "connected_to": ["equipment or line tags"],
      "page_number": {page_number},
      "bbox": [x1, y1, x2, y2],
      "attributes": {{}}
    }}
  ],
  "equipment": [
    {{
      "equipment_tag": "e.g. P-101A",
      "equipment_type": "e.g. Centrifugal Pump",
      "identification": "full description",
      "capacity": "e.g. 50 m3/h or 200 GPM (if available)",
      "other_data": {{
        "material": "",
        "design_pressure": "",
        "design_temperature": "",
        "power": "",
        "notes": ""
      }},
      "page_number": {page_number},
      "bbox": [x1, y1, x2, y2]
    }}
  ],
  "pipe_runs": [
    {{
      "pipe_run_tag": "e.g. 4\\"-P-1012-A1A",
      "size": "e.g. 4\\"",
      "fluid_code": "e.g. P (Process), CW (Cooling Water), etc.",
      "pipe_material_spec": "e.g. A106 Gr.B / CS",
      "insulation": "yes/no + type if available",
      "insulation_thickness": "e.g. 50 mm",
      "other_information": {{
        "piping_class": "",
        "from": "",
        "to": "",
        "design_pressure": "",
        "design_temperature": "",
        "notes": ""
      }},
      "page_number": {page_number}
    }}
  ],
  "piping_components": [
    {{
      "component_tag": "e.g. V-1012 or XV-101",
      "piping_component_type": "e.g. Gate Valve, Check Valve, Reducer, Elbow, Strainer",
      "connected_pipe_run": "pipe_run_tag it belongs to",
      "size": "e.g. 4\\"",
      "other_information": {{
        "rating": "e.g. 150#",
        "material": "",
        "end_connection": "Flanged / BW / SW",
        "notes": ""
      }},
      "page_number": {page_number},
      "bbox": [x1, y1, x2, y2]
    }}
  ],
  "unknown_symbols": [
    {{
      "description": "what the symbol looks like / what it might be, in your own words",
      "nearby_text": "any tag or text near it, if legible",
      "possible_category": "best guess at a category, if any, else empty string",
      "page_number": {page_number},
      "bbox": [x1, y1, x2, y2]
    }}
  ]
}}

If a table (or unknown_symbols) has no matching items on the page, return \
an empty list for it — never omit the key. If the image contains no \
recognizable P&ID content at all, return all five arrays empty."""


def _crop_and_encode(image_path: str, region: Optional[tuple[float, float, float, float]]) -> tuple[str, int, int]:
    """Returns (base64_png, width, height) of the region actually sent to the model."""
    from PIL import Image
    with Image.open(image_path) as img:
        if region:
            x1, y1, x2, y2 = region
            img = img.crop((x1, y1, x2, y2))
        width, height = img.size
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8"), width, height


def _offset_bboxes(result: PidExtractionResult, region: Optional[tuple[float, float, float, float]]) -> None:
    """
    The model only ever sees the cropped region (if one was given) and
    reports bboxes relative to it. Shift them back into full-page pixel
    coordinates so callers get consistent coordinates regardless of whether
    a region or the full page was analyzed — matching the CV pipeline's
    coordinate space (app/services/cv/symbol_detector.py). pipe_runs have
    no bbox field, so nothing to offset there.
    """
    if not region:
        return
    ox, oy = region[0], region[1]
    for group in (result.instruments, result.equipment, result.piping_components, result.unknown_symbols):
        for item in group:
            if len(item.bbox) == 4 and item.bbox != [0.0, 0.0, 0.0, 0.0]:
                item.bbox = [item.bbox[0] + ox, item.bbox[1] + oy, item.bbox[2] + ox, item.bbox[3] + oy]


def _force_page_numbers(result: PidExtractionResult, page_number: int) -> None:
    """
    page_number is caller-supplied context (we know which page/image we
    sent), not something the model should be trusted to infer — overwrite
    whatever it returned rather than relying on it getting this right.
    """
    for group in (result.instruments, result.equipment, result.pipe_runs, result.piping_components, result.unknown_symbols):
        for item in group:
            item.page_number = page_number


def _call_anthropic(image_b64: str, user_prompt: str, model: str) -> str:
    import anthropic
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=model,
        max_tokens=8192,
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
        max_tokens=8192,
    )
    return response.choices[0].message.content or ""


def extract_pid_tables(
    image_path: str,
    region: Optional[tuple[float, float, float, float]] = None,
    model: Optional[str] = None,
    page_number: int = 1,
) -> PidExtractionResult:
    """
    Runs the expert-analyst extraction pass on a P&ID page image — or, if
    `region` (x1, y1, x2, y2 in the source image's pixel coordinates) is
    given, on just that cropped region — and returns a validated
    PidExtractionResult with the four tables: instruments, equipment,
    pipe_runs, piping_components.

    Returned bboxes are always in the ORIGINAL full-page pixel coordinate
    space, even when a region was analyzed (the crop offset is added back
    in), so results line up with the CV pipeline's coordinates. Every
    row's page_number is forced to the caller-supplied `page_number`
    rather than trusting the model's own guess.

    Retries with a corrective prompt if the model's response isn't valid
    JSON or doesn't match the schema (OLLAMA_JSON_RETRY_ATTEMPTS extra
    attempts for Ollama); if it still can't get a parseable/valid result,
    returns an empty result with `extraction_notes` explaining why, rather
    than raising, so a single bad page/region doesn't take down a batch.
    """
    settings = get_settings()
    provider = settings.VLM_PROVIDER

    image_b64, width, height = _crop_and_encode(image_path, region)
    user_prompt = _build_user_prompt(width, height, page_number)
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
                    temperature=0.1,
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
                logger.warning("vlm_expert_analyst_disabled", extra={"context": {"provider": provider}})
                return PidExtractionResult(
                    region_analyzed=list(region) if region else None,
                    extraction_notes=f"No VLM provider configured (VLM_PROVIDER={provider}); page not analyzed.",
                )
        except OllamaUnavailableError as exc:
            logger.error("ollama_expert_analyst_failed", extra={"context": {"error": str(exc), "attempt": attempt}})
            if attempt == max_attempts:
                return PidExtractionResult(
                    region_analyzed=list(region) if region else None,
                    extraction_notes=f"Ollama unavailable: {exc}",
                )
            continue

        parsed = extract_json(raw_text)
        if parsed is None:
            logger.warning("vlm_response_not_json", extra={"context": {
                "attempt": attempt, "raw_text_length": len(raw_text),
                "raw_text_start": raw_text[:300], "raw_text_end": raw_text[-300:],
            }})
            continue

        try:
            validated = PidExtractionResult.model_validate(parsed)
            validated.region_analyzed = list(region) if region else None
            validated.model_used = used_model
            validated.parse_attempts = attempt
            _offset_bboxes(validated, region)
            _force_page_numbers(validated, page_number)
            return validated
        except ValidationError as exc:
            logger.warning("vlm_response_schema_mismatch", extra={"context": {
                "attempt": attempt, "error": str(exc)[:500], "raw_text_preview": raw_text[:500],
            }})
            continue

    return PidExtractionResult(
        region_analyzed=list(region) if region else None,
        parse_attempts=max_attempts,
        extraction_notes=f"Model returned no valid/parseable JSON after {max_attempts} attempt(s). Raw response was discarded.",
    )
