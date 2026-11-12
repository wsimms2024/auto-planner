"""
One way to get structured answers from Claude that works on every model.

Some models refuse a forced tool call (tool_choice "tool"/"any"), so this offers
the tool, asks Claude to use it, and if Claude answers in text instead, pulls
the JSON out of the text. Used by Prep, exam finding and syllabus weights.
"""

import json
import os
import re

from autoplan import config


class NoAnswer(ValueError):
    """Claude replied without the requested data."""


def client():
    import anthropic

    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def structured(system: str, content: str, tool: dict, max_tokens: int = 2000, api=None) -> dict:
    """Return the tool input Claude produced (a dict matching tool['input_schema'])."""
    api = api or client()
    response = api.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=f"{system}\n\nAnswer by calling the {tool['name']} tool. If you can't call it, reply with only the JSON object it expects.",
        tools=[tool],
        messages=[{"role": "user", "content": content}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
            return dict(block.input)
    text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text")
    return parse_json(text)


def parse_json(text: str) -> dict:
    """The first JSON object in a reply (handles ```json fences and surrounding words)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0:
                candidates.append(text[start:i + 1])
                break
    for c in candidates:
        try:
            value = json.loads(c)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise NoAnswer("Claude didn't return the expected data. Try again.")
