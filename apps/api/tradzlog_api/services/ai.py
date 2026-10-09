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


# Models that accept server-side refusal fallbacks ("default" routes by refusal category).
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}


def coaching_text(message: object) -> str:
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) or "unspecified"
        return f"Coaching could not be generated for this data (declined: {category}). Try again later."
    text = "\n".join(block.text for block in message.content if block.type == "text")
    if stop_reason == "max_tokens":
        text += "\n\n(Analysis was cut short.)"
    return text


NOT_CONFIGURED = "AI coaching isn't switched on for this site yet. Your data was not sent anywhere."

CHAT_SYSTEM_PROMPT = """You are a trading performance coach inside TradzLog, a trading journal, talking to {user_name}.
Answer their question using only the trading data provided between <trading_data> tags: their own closed trades,
grouped results and recent trades. Quote the numbers that support your answer. If the data can't answer the question,
say so and say what they could log to answer it next time.
Keep it short: a direct answer first, then at most five short points. Plain text, no tables, no markdown headings.
You review past trading behaviour. Never recommend buying or selling a specific instrument, never predict prices,
and never present anything as financial advice."""


def ask_claude(system: str, content: str, max_tokens: int) -> str:
    """One request to Claude with the site's model; refusals and truncation come back as readable text."""
    client = Anthropic(api_key=settings.anthropic_api_key)
    request = {
        "model": settings.anthropic_model,
        # Thinking is on by default on current models and counts toward max_tokens.
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }
    if settings.anthropic_model in FALLBACK_MODELS:
        message = client.beta.messages.create(**request, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    else:
        message = client.messages.create(**request)
    return coaching_text(message)


def answer_question(user_name: str, question: str, trading_data: str) -> str:
    """Answer a trader's question about their own results ("Ask the coach")."""
    if not settings.anthropic_api_key:
        return NOT_CONFIGURED
    content = f"<trading_data>\n{trading_data}\n</trading_data>\n\nQuestion: {question.strip()[:1000]}"
    return ask_claude(CHAT_SYSTEM_PROMPT.format(user_name=user_name), content, 8000)


def generate_coaching_insight(
    user_id: str,
    user_name: str,
    account_id: str | None,
    payload: str,
) -> AIInsight:
    content = ask_claude(SYSTEM_PROMPT.format(user_name=user_name), payload, 16000) if settings.anthropic_api_key else NOT_CONFIGURED
    return AIInsight(
        user_id=user_id,
        account_id=account_id,
        type=AIInsightType.PATTERN,
        title="Trading pattern analysis",
        content=content,
        supporting_trade_ids=[],
        generated_at=datetime.now(UTC),
    )
