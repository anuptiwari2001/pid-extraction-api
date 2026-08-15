"""
Stage 3: Text extraction.

Design choice, and why it diverges slightly from "run a VLM on every
region": OCR (EasyOCR/Tesseract) is essentially free and highly accurate on
clean, vector-quality P&ID text (tags, line numbers are drafted text, not
handwriting). Calling a vision-language model per detected region at scale
is real money and real latency for no accuracy gain on that majority case.

So: OCR runs on every region + the full page. A VLM call (Claude or GPT-4o
vision) is only made for a crop when OCR confidence is low or returns
nothing — e.g. handwritten markups, degraded scans, rotated text stacked in
an instrument bubble. This mirrors exactly how the unknown-symbol routing
works: cheap path first, expensive/human path only for the ambiguous tail.
"""
import base64
import re
from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# ISA-5.1 instrument tag pattern: 1-4 letter function code + hyphen/space + number,
# e.g. "FIC-101", "PT 204A", "LSH-305B"
ISA_TAG_PATTERN = re.compile(r"\b([A-Z]{1,4})[\s\-]?(\d{2,4}[A-Z]?)\b")


@dataclass
class TextRegion:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float]


class OcrEngine:
    def __init__(self, engine: str = "easyocr"):
        self.engine_name = engine
        self._reader = None

    def _get_reader(self):
        if self._reader is not None:
            return self._reader
        if self.engine_name == "easyocr":
            import easyocr
            self._reader = easyocr.Reader(["en"], gpu=False)
        return self._reader

    def extract(self, image_path: str) -> list[TextRegion]:
        if self.engine_name == "easyocr":
            reader = self._get_reader()
            raw = reader.readtext(image_path)
            regions = []
            for bbox_pts, text, conf in raw:
                xs = [p[0] for p in bbox_pts]
                ys = [p[1] for p in bbox_pts]
                regions.append(TextRegion(
                    text=text.strip(), confidence=float(conf),
                    bbox=(min(xs), min(ys), max(xs), max(ys)),
                ))
            return regions

        if self.engine_name == "tesseract":
            import pytesseract
            from PIL import Image
            image = Image.open(image_path)
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            regions = []
            for i, text in enumerate(data["text"]):
                text = text.strip()
                if not text:
                    continue
                conf = float(data["conf"][i]) / 100.0 if data["conf"][i] != "-1" else 0.0
                x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                regions.append(TextRegion(text=text, confidence=conf, bbox=(x, y, x + w, y + h)))
            return regions

        raise ValueError(f"Unsupported OCR engine: {self.engine_name}")


def _crop_to_base64(image_path: str, bbox: tuple[float, float, float, float]) -> str:
    from PIL import Image
    import io
    with Image.open(image_path) as img:
        crop = img.crop(bbox)
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")


def vlm_extract_text(image_path: str, bbox: tuple[float, float, float, float]) -> Optional[str]:
    """
    Fallback text extraction via a vision-language model for crops OCR
    couldn't confidently read. Returns None if no VLM provider is
    configured (VLM_PROVIDER=none) rather than failing the whole pipeline —
    that crop just keeps its low-confidence OCR result (or empty string).
    """
    settings = get_settings()
    if settings.VLM_PROVIDER == "none":
        return None

    crop_b64 = _crop_to_base64(image_path, bbox)

    if settings.VLM_PROVIDER == "ollama":
        from app.services.vlm.ollama_client import chat_with_image, OllamaUnavailableError
        try:
            result = chat_with_image(
                system_prompt=(
                    "You transcribe cropped regions of P&ID drawings. Return ONLY the exact "
                    "text visible in the image — an instrument tag, line number, or equipment "
                    "ID. No explanation, no punctuation beyond what's in the image. If nothing "
                    "legible is present, return an empty string."
                ),
                user_prompt="Transcribe the text in this P&ID crop.",
                image_b64=crop_b64,
                model=settings.OLLAMA_VLM_MODEL,
                force_json=False,  # plain text, not structured extraction
                temperature=0.0,
            )
            return result.raw_text.strip() or None
        except OllamaUnavailableError as exc:
            logger.warning("ollama_vlm_text_extract_failed", extra={"context": {"error": str(exc)}})
            return None

    if settings.VLM_PROVIDER == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.VLM_MODEL,
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": crop_b64}},
                    {"type": "text", "text": (
                        "This is a cropped region from a P&ID drawing. Return ONLY the exact text "
                        "visible in the image (an instrument tag, line number, or equipment ID). "
                        "No explanation, no punctuation beyond what's in the image."
                    )},
                ],
            }],
        )
        text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        return "".join(text_blocks).strip() or None

    if settings.VLM_PROVIDER == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "This is a cropped region from a P&ID drawing. Return ONLY the exact text "
                        "visible (an instrument tag, line number, or equipment ID)."
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{crop_b64}"}},
                ],
            }],
            max_tokens=100,
        )
        return (response.choices[0].message.content or "").strip() or None

    logger.warning("unknown_vlm_provider", extra={"context": {"provider": settings.VLM_PROVIDER}})
    return None


def parse_isa_tag(text: str) -> Optional[dict]:
    """Extract an ISA-5.1-style function-code + loop-number tag from raw OCR text, if present."""
    match = ISA_TAG_PATTERN.search(text.upper().replace(" ", "-"))
    if not match:
        return None
    return {"isa_type_code": match.group(1), "loop_number": match.group(2), "full_tag": f"{match.group(1)}-{match.group(2)}"}


def extract_text_for_page(image_path: str, low_confidence_threshold: float = 0.4) -> list[TextRegion]:
    """
    Full-page OCR pass, with a VLM top-up for any region OCR was unsure
    about. This is the entry point called once per page by the orchestrator;
    per-symbol text (e.g. matching a tag to its instrument bubble) is
    resolved downstream by spatial proximity, not by re-running OCR.
    """
    settings = get_settings()
    ocr = OcrEngine(engine=settings.OCR_ENGINE)
    regions = ocr.extract(image_path)

    for region in regions:
        if region.confidence < low_confidence_threshold or not region.text:
            better_text = vlm_extract_text(image_path, region.bbox)
            if better_text:
                region.text = better_text
                region.confidence = max(region.confidence, 0.6)  # VLM result, treat as moderately trusted

    return regions
