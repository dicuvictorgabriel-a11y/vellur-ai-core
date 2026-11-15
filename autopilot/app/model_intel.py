"""Model capability detection shared by provider discovery, routing and the dashboard.

Provider catalogs differ a lot. OpenRouter publishes `architecture.input_modalities` /
`architecture.output_modalities` and `supported_parameters`; other providers often publish almost nothing. We keep the
raw catalog shape stable (id/price/context) and store normalized capabilities separately, so an old client cannot be
surprised by extra fields. Unknown models get a conservative text/chat baseline: never claim vision or image output
without evidence.
"""
from __future__ import annotations

import re
from typing import Iterable

CAPABILITIES = ("text_input", "text_output", "chat", "coding", "reasoning", "tool_call", "vision",
                "image_generation", "image_edit", "audio_input", "audio_output", "embedding")

LABELS = {
    "text_input": "Text input",
    "text_output": "Text output",
    "chat": "Chat",
    "coding": "Coding",
    "reasoning": "Reasoning",
    "tool_call": "Tools",
    "vision": "Vision",
    "image_generation": "Image generation",
    "image_edit": "Image editing",
    "audio_input": "Audio input",
    "audio_output": "Audio output",
    "embedding": "Embeddings",
}

_ORDER = {c: i for i, c in enumerate(CAPABILITIES)}
_CODE_RE = re.compile(r"(?:^|[/_.-])(?:coder|code|codestral|devstral|deepseek[-_.]?c(?:oder)?|qwen\d*[-_.]?coder|"
                      r"starcoder|codegemma|codex)(?:$|[/_.-])", re.I)
_EMBED_RE = re.compile(r"(?:^|[/_.-])embed(?:ding)?(?:$|[/_.-])", re.I)
_VISION_RE = re.compile(r"(?:^|[/_.-])(?:vl|vision|llava|pixtral|molmo|internvl)(?:$|[/_.-])", re.I)
_IMAGE_RE = re.compile(r"(?:^|[/_.-])(?:seedream|dall[-_.]?e|stable[-_.]?(?:diffusion|image)|sdxl"
                       r"|flux|imagen|kandinsky|ideogram|recraft)"
                       r"(?:$|[/_.-])", re.I)


def _list(value) -> list[str]:
    if isinstance(value, str):
        return [x.strip().lower() for x in re.split(r"[, ]+", value) if x.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip().lower() for x in value if str(x).strip()]
    return []


def infer_capabilities(model_id: str, raw: dict | None = None, provider: str = "") -> list[str]:
    """Normalized capability names for one catalog row (conservative when metadata is absent)."""
    raw = raw if isinstance(raw, dict) else {}
    model = str(model_id or raw.get("id") or "")
    arch = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
    inputs = _list(arch.get("input_modalities") or raw.get("input_modalities") or raw.get("modalities"))
    outputs = _list(arch.get("output_modalities") or raw.get("output_modalities"))
    params = set(_list(raw.get("supported_parameters")))
    caps: set[str] = set()

    if inputs:
        if "text" in inputs:
            caps.add("text_input")
        if "image" in inputs:
            caps.add("vision")
        if "audio" in inputs:
            caps.add("audio_input")
    else:
        caps.add("text_input")

    if outputs:
        if "text" in outputs:
            caps.add("text_output")
        if "image" in outputs:
            caps.add("image_generation")
            if "image" in inputs:
                caps.add("image_edit")
        if "audio" in outputs:
            caps.add("audio_output")
    else:
        caps.add("text_output")

    if "tools" in params or "tool_choice" in params or raw.get("tool_call") is True:
        caps.add("tool_call")
    if "reasoning" in params or raw.get("reasoning") is True or "include_reasoning" in params:
        caps.add("reasoning")
    tags = " ".join(_list(raw.get("tags")) + _list(raw.get("categories")))
    if _CODE_RE.search(model) or "coding" in tags or "code" in tags:
        caps.add("coding")
    if _EMBED_RE.search(model) or "embedding" in tags:
        caps.add("embedding")
        caps.discard("chat")
    if _VISION_RE.search(model):
        caps.add("vision")
    if _IMAGE_RE.search(model):
        caps.add("image_generation")
    if "chat" in caps or ("text_input" in caps and "text_output" in caps and "embedding" not in caps):
        caps.add("chat")
    return sorted(caps, key=lambda c: _ORDER.get(c, 999))


def labels(caps: Iterable[str]) -> list[str]:
    return [LABELS.get(c, c) for c in caps]


def is_image_target(caps: Iterable[str]) -> bool:
    return "image_generation" in set(caps)


def summary(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    return {
        "models": len(rows),
        "vision": sum(1 for r in rows if "vision" in (r.get("capabilities") or [])),
        "image_generation": sum(1 for r in rows if "image_generation" in (r.get("capabilities") or [])),
        "tool_call": sum(1 for r in rows if "tool_call" in (r.get("capabilities") or [])),
        "coding": sum(1 for r in rows if "coding" in (r.get("capabilities") or [])),
    }
