"""
Thin client for a local/self-hosted Ollama server.

Deliberately NOT using the `ollama` pip package so there's one less
dependency to install — this is a small wrapper around Ollama's plain HTTP
API (`/api/chat`) using `requests`. Ollama has no concept of an API key: it's
either reachable on OLLAMA_BASE_URL or it isn't, so there is nothing to
authenticate and nothing to leak. Do not add an Authorization header or any
key/secret here.

Requires the target model to already be pulled on the Ollama host, e.g.:
    ollama pull qwen3-vl:4b
    ollama serve
"""
import base64
import json
import time
from dataclasses import dataclass
from typing import Optional

import requests

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class OllamaUnavailableError(Exception):
    """Raised when the Ollama server can't be reached or returns an error."""


@dataclass
class OllamaChatResult:
    raw_text: str
    model: str


def _image_path_to_b64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _post_with_retry(
    url: str,
    payload: dict,
    timeout_seconds: int,
    max_retries: int = 2,
    base_delay: float = 1.0,
) -> requests.Response:
    """
    Post to Ollama with exponential backoff retry on transient failures
    (timeout, connection error, 5xx). Does NOT retry on 404 (model not found)
    or 400 (format error).
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=timeout_seconds)
            # Don't retry on client errors (4xx) except retryable ones
            if 400 <= resp.status_code < 500:
                if resp.status_code not in (408,):  # 408 is Timeout (retryable)
                    return resp
            return resp
        except requests.exceptions.Timeout as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "ollama_timeout_retry",
                    extra={
                        "context": {
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "delay_seconds": delay,
                        }
                    },
                )
                time.sleep(delay)
        except requests.exceptions.ConnectionError as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "ollama_connection_retry",
                    extra={
                        "context": {
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "delay_seconds": delay,
                        }
                    },
                )
                time.sleep(delay)
        except requests.exceptions.RequestException as exc:
            # Re-raise non-retryable exceptions immediately
            raise exc

    # If we exhausted retries, raise the last exception
    if last_exc:
        raise last_exc
    # Should not reach here, but fallback to last response
    return resp


def chat_with_image(
    system_prompt: str,
    user_prompt: str,
    image_path: Optional[str] = None,
    image_b64: Optional[str] = None,
    model: Optional[str] = None,
    force_json: bool = True,
    temperature: float = 0.1,
    num_ctx: Optional[int] = None,
    num_gpu: Optional[int] = None,
    timeout_seconds: Optional[int] = None,
) -> OllamaChatResult:
    """
    Sends a single-turn chat request with an optional image to Ollama.

    Exactly one of image_path / image_b64 should be given when an image is
    part of the request. force_json=True sets Ollama's `format: "json"`
    option, which constrains decoding to syntactically valid JSON (it does
    NOT guarantee the JSON matches our schema — callers still validate the
    parsed structure themselves).

    num_ctx is always sent explicitly (settings.OLLAMA_NUM_CTX if not
    overridden here) rather than left to the model's own default context
    window — a full P&ID page image plus the extraction prompt easily
    exceeds Ollama's common 4096-token default, which fails the request
    outright (HTTP 400 "exceeds the available context size") instead of
    truncating. This makes the fix apply to whichever model is configured,
    with no dependency on a hand-built Modelfile/custom model tag.

    num_gpu is likewise always sent explicitly (settings.OLLAMA_NUM_GPU if
    not overridden here) — Ollama's own VRAM auto-detection can come back
    overly conservative on hybrid-graphics laptops and silently fall back
    to 100% CPU with no error, just ~2-3 tokens/sec instead of the 20-40+
    a small model should get on a real GPU. A large explicit value asks it
    to offload as many layers as will actually fit.

    Low temperature by default: this is a structured-extraction task, not a
    creative one — we want the same drawing to yield the same tags every
    time, not stylistic variety.
    """
    settings = get_settings()
    model = model or settings.OLLAMA_VLM_MODEL
    num_ctx = num_ctx or settings.OLLAMA_NUM_CTX
    num_gpu = settings.OLLAMA_NUM_GPU if num_gpu is None else num_gpu
    timeout_seconds = timeout_seconds or settings.OLLAMA_TIMEOUT_SECONDS

    if image_path and not image_b64:
        image_b64 = _image_path_to_b64(image_path)

    messages = [{"role": "system", "content": system_prompt}]
    user_message: dict = {"role": "user", "content": user_prompt}
    if image_b64:
        user_message["images"] = [image_b64]
    messages.append(user_message)

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": num_ctx, "num_gpu": num_gpu},
    }
    if force_json:
        payload["format"] = "json"

    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"

    try:
        resp = _post_with_retry(url, payload, timeout_seconds, max_retries=2, base_delay=1.0)
    except requests.exceptions.RequestException as exc:
        logger.error("ollama_unreachable", extra={"context": {"url": url, "error": str(exc)}})
        raise OllamaUnavailableError(
            f"Could not reach Ollama at {settings.OLLAMA_BASE_URL}. "
            f"Is `ollama serve` running and is '{model}' pulled? ({exc})"
        ) from exc

    if resp.status_code == 404:
        raise OllamaUnavailableError(
            f"Ollama model '{model}' not found on {settings.OLLAMA_BASE_URL}. "
            f"Pull it first with: ollama pull {model}"
        )
    if resp.status_code != 200:
        raise OllamaUnavailableError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        content = data["message"]["content"]
    except (ValueError, KeyError) as exc:
        raise OllamaUnavailableError(f"Unexpected Ollama response shape: {resp.text[:500]}") from exc

    return OllamaChatResult(raw_text=content, model=model)


def extract_json(raw_text: str) -> Optional[dict]:
    """
    Best-effort JSON parse of a model response. Even with format="json",
    some Ollama models wrap output in markdown fences or add stray
    whitespace/preamble — strip that before giving up.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Last resort: grab the outermost {...} span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None
