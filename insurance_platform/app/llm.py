"""LLM-backed risk-factor discovery via the official Anthropic SDK.

Given a shipment, ask Claude to enumerate the material risk factors that could
delay the cargo or impair its value, each with an estimated probability, a
loss-impact fraction, and a search query used to find a matching prediction
market. Returns raw dicts; the discovery layer normalises them.

Any failure (no credentials, blocked network, malformed output) raises — the
FallbackDiscovery wrapper then transparently falls back to the P1 rule engine,
so the platform keeps working offline / without an API key.
"""
from __future__ import annotations

import json
import logging

from .config import settings

logger = logging.getLogger(__name__)

# JSON Schema the model must satisfy (structured outputs).
FACTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "factors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "label": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["route", "geopolitical", "macro", "weather"],
                    },
                    "probability": {"type": "number"},
                    "impact": {"type": "number"},
                    "search_query": {"type": "string"},
                },
                "required": [
                    "key", "label", "category", "probability", "impact", "search_query",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["factors"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are the risk-analysis engine of an insurer that hedges cargo shipments "
    "using prediction markets. Given a shipment, identify the material risk "
    "factors that could delay on-time delivery or impair the cargo's value. "
    "Cover route/chokepoint, geopolitical, macro (rates/FX/fuel), and weather "
    "categories where relevant. For each factor estimate: probability it "
    "materialises before the deadline (0-1), impact as the fraction of cargo "
    "value at risk if it does (0-1, keep individually modest), and a concise "
    "search_query to find the matching prediction-market book. Return 4-8 of the "
    "most material factors."
)


def _extract_json(response) -> dict:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text = block.text.strip()
            if text.startswith("```"):
                text = text.strip("`")
                text = text[text.find("\n") + 1 :] if "\n" in text else text
            return json.loads(text)
    raise ValueError("no text block in LLM response")


def discover_factors_llm(shipment_like, cargo_value: float) -> list[dict]:
    """Call Claude to discover risk factors. Raises on any failure."""
    import anthropic  # imported lazily so the app runs without the dep installed

    client = anthropic.Anthropic()
    user = (
        f"Shipment:\n"
        f"- cargo value (USD): {cargo_value}\n"
        f"- origin: {getattr(shipment_like, 'origin', '')}\n"
        f"- destination: {getattr(shipment_like, 'destination', '')}\n"
        f"- route: {getattr(shipment_like, 'route', '') or 'unspecified'}\n"
        f"- cargo type: {getattr(shipment_like, 'cargo_type', '') or 'unspecified'}\n"
        f"- deadline: {getattr(shipment_like, 'deadline', '') or 'unspecified'}\n\n"
        "Return the factors as JSON."
    )
    response = client.messages.create(
        model=settings.llm_model,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": FACTOR_SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    data = _extract_json(response)
    factors = data.get("factors", []) if isinstance(data, dict) else data
    if not isinstance(factors, list) or not factors:
        raise ValueError("LLM returned no factors")
    return factors
