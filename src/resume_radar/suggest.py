"""Optional LLM pass that rewrites the bullets the deterministic checks flagged.

This module never runs on its own. The caller must supply an API key, and if none
is present the analysis still completes with the deterministic score intact - the
model is an enhancement, not a dependency.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_ENDPOINT = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"

PROMPT = """You are reviewing bullets from a student's software engineering resume.

Rewrite each bullet so that it:
- stays under 30 words
- opens with a strong past-tense action verb
- keeps every number, tool, and factual claim exactly as given
- invents nothing that is not already stated

Do not add metrics that are not present. If a bullet has no measurable outcome,
improve the verb and structure only.

Return strict JSON: {"rewrites": [{"original": "...", "suggested": "...", "why": "..."}]}

Bullets:
%s
"""


@dataclass
class Suggestion:
    original: str
    suggested: str
    why: str

    def to_dict(self) -> dict:
        return {"original": self.original, "suggested": self.suggested, "why": self.why}


class SuggestionUnavailable(RuntimeError):
    """Raised when no API key is configured."""


def weak_bullets(doc, report, limit: int = 6) -> list[str]:
    """Pick the bullets most worth rewriting, using the failing checks as the guide."""
    flagged: list[str] = []
    by_key = {c.key: c for c in report.checks}

    for key in ("long_bullets", "quantified", "action_verbs"):
        check = by_key.get(key)
        if check and not check.passed:
            flagged.extend(check.evidence)

    if not flagged:
        flagged = sorted(doc.bullets, key=lambda b: -len(b.split()))[:limit]

    seen, out = set(), []
    for bullet in flagged:
        match = next((b for b in doc.bullets if b.startswith(bullet[:40])), bullet)
        if match not in seen:
            seen.add(match)
            out.append(match)
    return out[:limit]


def rewrite(bullets: list[str], *, api_key: str | None = None,
            model: str = DEFAULT_MODEL, timeout: int = 60) -> list[Suggestion]:
    """Ask a model to tighten ``bullets``. Raises if no key is configured."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SuggestionUnavailable(
            "set ANTHROPIC_API_KEY to enable rewrite suggestions; "
            "the deterministic score works without it"
        )
    if not bullets:
        return []

    body = json.dumps({
        "model": model,
        "max_tokens": 1500,
        "messages": [{"role": "user",
                      "content": PROMPT % "\n".join(f"- {b}" for b in bullets)}],
    }).encode()

    request = urllib.request.Request(
        DEFAULT_ENDPOINT,
        data=body,
        headers={"content-type": "application/json",
                 "x-api-key": key,
                 "anthropic-version": "2023-06-01"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.URLError as exc:  # pragma: no cover - network dependent
        raise SuggestionUnavailable(f"model request failed: {exc}") from exc

    text = "".join(block.get("text", "") for block in payload.get("content", []))
    return parse_rewrites(text)


def parse_rewrites(text: str) -> list[Suggestion]:
    """Pull suggestions out of a model reply, tolerating fenced code blocks."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [
        Suggestion(item.get("original", ""), item.get("suggested", ""), item.get("why", ""))
        for item in data.get("rewrites", [])
        if item.get("suggested")
    ]
