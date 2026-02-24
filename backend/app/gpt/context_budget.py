import json
import os
import re
from typing import Dict, Optional

_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def estimate_tokens(text: Optional[str]) -> int:
    """
    Rough token estimate without introducing a tokenizer dependency.
    Chinese chars usually consume more tokens than latin words.
    """
    if not text:
        return 0

    total_chars = len(text)
    cjk_chars = len(_CJK_RE.findall(text))
    latin_chars = max(total_chars - cjk_chars, 0)

    # Heuristic:
    # - CJK text: ~1.2 tokens per char
    # - Latin text: ~1 token per 4 chars
    return max(int(cjk_chars * 1.2 + latin_chars / 4), 1)


def _parse_overrides(raw: Optional[str]) -> Dict[str, int]:
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    parsed: Dict[str, int] = {}
    for key, value in data.items():
        if not key:
            continue
        try:
            parsed[str(key)] = int(value)
        except Exception:
            continue
    return parsed


def resolve_context_limit(model_name: str) -> int:
    default_limit = int(os.getenv("MODEL_CONTEXT_LIMIT_DEFAULT", "32000"))
    overrides = _parse_overrides(os.getenv("MODEL_CONTEXT_LIMIT_OVERRIDES"))
    if not model_name:
        return default_limit

    normalized = model_name.strip()
    normalized_lower = normalized.lower()

    for candidate in (normalized, normalized_lower):
        if candidate in overrides:
            return overrides[candidate]

    # Support prefixed model names such as "openai/gpt-4o-mini".
    if "/" in normalized:
        short_name = normalized.split("/")[-1].strip()
        short_name_lower = short_name.lower()
        for candidate in (short_name, short_name_lower):
            if candidate in overrides:
                return overrides[candidate]

    return default_limit


def is_overflow(estimated_input: int, context_limit: int, reserve_output: int = 4000) -> bool:
    budget = max(context_limit - max(reserve_output, 0), 1)
    return estimated_input > budget
