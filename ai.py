"""AI classification of a company from its homepage text (Claude, structured output)."""
import logging
import os
from typing import Literal

from pydantic import BaseModel

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"


class AIFit(BaseModel):
    industry: str
    services: list[str]
    icp_summary: str
    b2b_or_b2c: Literal["b2b", "b2c", "both", "unknown"]
    size_estimate: str
    confidence: float


def classify_with_claude(text: str) -> dict | None:
    """Return the `ai` signals object, or None if there's no API key or the call fails."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic

        resp = anthropic.Anthropic(api_key=key).messages.parse(
            model=MODEL,
            max_tokens=1024,
            output_config={"effort": "low"},
            output_format=AIFit,
            system="You classify companies from their homepage text for a marketing database. "
            "size_estimate is an employee range like '11-50'; confidence is 0-1.",
            messages=[{"role": "user", "content": text}],
        )
        return resp.parsed_output.model_dump()
    except Exception:
        log.exception("AI classification failed")
        return None
