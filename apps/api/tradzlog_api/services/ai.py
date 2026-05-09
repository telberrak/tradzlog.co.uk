from __future__ import annotations

import json
from datetime import UTC, datetime

from anthropic import Anthropic

from tradzlog_api.config import settings
from tradzlog_db.models import AIInsight, AIInsightType

SYSTEM_PROMPT = """You are a professional trading coach and performance analyst for {user_name}.
You have access to their complete trading performance data. Be direct, specific, and data-driven.
Never give generic advice. Every insight must reference their actual statistics.
Tone: Professional, honest, direct. Like a senior prop trader reviewing a junior's book.
Format: Return JSON matching the AIInsight schema. Include 3-5 insights."""


def build_coaching_payload(summary: dict[str, object], setup_breakdown: list[dict[str, object]]) -> str:
    return json.dumps(
        {
            "prompt": "Analyse the following trading data and identify behavioural and statistical patterns.",
            "performanceSummary": summary,
            "setupBreakdown": setup_breakdown,
        },
        default=str,
    )


def generate_coaching_insight(
    user_id: str,
    user_name: str,
    account_id: str | None,
    payload: str,
) -> AIInsight:
    if settings.anthropic_api_key:
        client = Anthropic(api_key=settings.anthropic_api_key)
        message = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1800,
            system=SYSTEM_PROMPT.format(user_name=user_name),
            messages=[{"role": "user", "content": payload}],
        )
        content = "\n".join(block.text for block in message.content if block.type == "text")
    else:
        content = (
            "AI coaching is wired but ANTHROPIC_API_KEY is not configured. "
            f"Payload prepared for Claude: {payload[:1200]}"
        )
    return AIInsight(
        user_id=user_id,
        account_id=account_id,
        type=AIInsightType.PATTERN,
        title="Trading pattern analysis",
        content=content,
        supporting_trade_ids=[],
        generated_at=datetime.now(UTC),
    )
