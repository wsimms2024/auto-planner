"""
The planner you text. Sends your message to Claude along with the planner
tools; Claude looks things up (or makes changes) and replies in plain text.

Keeps a short memory per chat so follow-ups like "ok put it on my calendar"
work. The memory resets after a couple of hours of quiet.
"""

import json
import os
from datetime import datetime, timedelta

from autoplan import config, planner_tools

MAX_TOOL_ROUNDS = 6
HISTORY_TURNS = 12
FORGET_AFTER = timedelta(hours=2)

SYSTEM = """You are Will's personal day planner, built into his AutoPlan app. He texts you from his phone.

Right now it is {now}. He's a GWU student in Washington, DC (Eastern time).

How to answer:
- Always look things up with the tools before answering. Never guess at his schedule, tasks, or free time.
- Keep replies short, like a text: a few lines, plain text, no tables, no markdown headers. Times like 3:15 PM.
- When he asks when to do something, give one clear recommendation and a backup, with the reason in a few words.
- Only change things (add tasks, events, or study sessions; mark done) when he asks or clearly agrees. Say what you changed.
- To change or delete an event, call find_events first to get its event_id. If several match, ask which one.
- For weekly events, change or cancel just the one day unless he says every week.
- Deleting is fine when he clearly asks ("cancel", "delete", "remove"). If it's unclear, confirm first.
- If a task name is ambiguous, ask which one.
"""


class Assistant:
    def __init__(self, client=None, model: str | None = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.client = client
        self.model = model or config.CLAUDE_MODEL
        self.history: dict[str, list] = {}
        self.last_seen: dict[str, datetime] = {}

    def reset(self, chat_id: str) -> None:
        self.history.pop(chat_id, None)

    def reply(self, chat_id: str, text: str, now: datetime | None = None) -> str:
        now = now or datetime.now()
        if chat_id in self.last_seen and now - self.last_seen[chat_id] > FORGET_AFTER:
            self.reset(chat_id)
        self.last_seen[chat_id] = now

        messages = self.history.get(chat_id, []) + [{"role": "user", "content": text}]
        system = SYSTEM.format(now=now.strftime("%A, %B %d, %Y, %I:%M %p"))

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=900,
                system=system,
                tools=planner_tools.TOOLS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})
            tool_calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_calls:
                break
            results = []
            for call in tool_calls:
                output = planner_tools.run_tool(call.name, call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(output, default=str)})
            messages.append({"role": "user", "content": results})
        else:
            answer = "That took too many steps. Try asking a bit more specifically."
            self._remember(chat_id, text, answer)
            return answer

        answer = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip() or "Done."
        self._remember(chat_id, text, answer)
        return answer

    def note(self, chat_id: str, sent_text: str) -> None:
        """Remember a message the bot sent on its own, so replies like "book it" make sense."""
        self._remember(chat_id, "(AutoPlan sent this automatically)", sent_text)
        self.last_seen[chat_id] = datetime.now()

    def _remember(self, chat_id: str, question: str, answer: str) -> None:
        """Keep only the plain question/answer text (not tool details) to stay small and cheap."""
        turns = self.history.get(chat_id, []) + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]
        self.history[chat_id] = turns[-HISTORY_TURNS * 2:]
