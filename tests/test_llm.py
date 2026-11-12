"""Structured answers from Claude without forcing a tool call."""

from types import SimpleNamespace

import pytest

from autoplan import llm

TOOL = {"name": "save_x", "description": "", "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}}}}


class Fake:
    def __init__(self, content):
        self.content, self.messages = content, self

    def create(self, **kw):
        self.sent = kw
        return SimpleNamespace(content=self.content)


def test_tool_call_answer():
    fake = Fake([SimpleNamespace(type="tool_use", name="save_x", input={"a": 1}, id="t")])
    assert llm.structured("s", "c", TOOL, api=fake) == {"a": 1}
    assert "tool_choice" not in fake.sent and "save_x" in fake.sent["system"]


@pytest.mark.parametrize("text", [
    '{"a": 2}',
    'Here you go:\n```json\n{"a": 2}\n```',
    'Sure. {"a": 2} Let me know.',
])
def test_text_json_answer(text):
    assert llm.structured("s", "c", TOOL, api=Fake([SimpleNamespace(type="text", text=text)])) == {"a": 2}


def test_no_answer_raises_clear_error():
    with pytest.raises(llm.NoAnswer):
        llm.structured("s", "c", TOOL, api=Fake([SimpleNamespace(type="text", text="I couldn't find anything.")]))
