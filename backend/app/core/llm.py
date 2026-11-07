"""LLM extraction (Anthropic). Swap this one function to use another provider."""
import importlib.util
import json
import re
from typing import Optional

from .config import settings
from .errors import TransientError
from .prompts import SYSTEM_PROMPT, USER_TEMPLATE


def llm_available() -> bool:
    return bool(settings.use_llm and settings.anthropic_api_key and importlib.util.find_spec("anthropic"))


def parse_json_object(raw: str) -> Optional[dict]:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def extract_with_llm(text: str) -> Optional[dict]:
    """Returns parsed JSON dict, None if the model output is unusable. Raises TransientError for retryable faults."""
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=90)
    try:
        msg = client.messages.create(
            model=settings.llm_model, max_tokens=2500, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": USER_TEMPLATE.format(text=text[:settings.llm_max_chars])}])
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APITimeoutError, anthropic.InternalServerError) as e:
        raise TransientError(f"LLM temporarily unavailable: {e}")
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return parse_json_object(raw)
