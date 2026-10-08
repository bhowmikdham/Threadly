"""Explicit Haiku 5.5 request compatibility; no model selection or fallback."""

HAIKU_45 = "anthropic.claude-haiku-4-5-20251001-v1:0"
HAIKU_55 = "anthropic.claude-haiku-5-5"
AU_PROFILES = ("au." + HAIKU_45, "au." + HAIKU_55)


def is_haiku_55(model: str) -> bool:
    return model.rsplit("/", 1)[-1] in {HAIKU_55, "au." + HAIKU_55}


def request_options(model: str, max_tokens: int, *, temperature=None) -> dict:
    """Keep the existing non-thinking behavior and allow for the new tokenizer.

    5.5 rejects the legacy temperature and defaults to adaptive thinking. These
    bounded extraction/draft calls explicitly disable thinking. No token-count
    endpoint, new region or additional IAM action is needed.
    """
    if is_haiku_55(model):
        return {
            "inferenceConfig": {"maxTokens": (max_tokens * 13 + 9) // 10},
            "additionalModelRequestFields": {"thinking": {"type": "disabled"}},
        }
    config = {"maxTokens": max_tokens}
    if temperature is not None:
        config["temperature"] = temperature
    return {"inferenceConfig": config}
