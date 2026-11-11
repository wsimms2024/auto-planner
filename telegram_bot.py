"""
AutoPlan on Telegram. Run it next to the Streamlit app:

    python telegram_bot.py

Text it questions like "when should I go to the gym today?" or "anything due
for ISTM 4205 tomorrow?". It also sends a morning briefing, asks after study
sessions whether you did them, and keeps Google/Blackboard synced, even when
the Streamlit app isn't open.

Needs TELEGRAM_BOT_TOKEN and ANTHROPIC_API_KEY in .env. It only answers the
chat id in TELEGRAM_CHAT_ID; send /start once to find yours.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from autoplan import briefing, config, db, planner_tools
from autoplan.assistant import Assistant
from autoplan.integrations import sync

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("autoplan.bot")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CHECK_EVERY_SECONDS = 300
BRIEFING_GRACE = timedelta(hours=3)  # if the computer was off at 8:00, still send it until 11:00

assistant: Assistant | None = None


def is_owner(update: Update) -> bool:
    return bool(CHAT_ID) and str(update.effective_chat.id) == CHAT_ID


def get_assistant() -> Assistant:
    global assistant
    if assistant is None:
        assistant = Assistant()
    return assistant


# ---------- Commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = str(update.effective_chat.id)
    if not CHAT_ID:
        await update.message.reply_text(
            f"Your chat id is {chat}.\nPut TELEGRAM_CHAT_ID={chat} in the .env file, restart the bot, and I'm all yours."
        )
        return
    if not is_owner(update):
        return
    await update.message.reply_text(
        "AutoPlan here. Ask me things like:\n"
        "• when should I go to the gym today?\n"
        "• anything due for ISTM 4205 tomorrow?\n"
        "• when should I start the final project?\n\n"
        "/today  today's schedule\n/tasks  what's due this week\n/brief  morning briefing\n/reset  start a fresh conversation"
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    s = planner_tools.get_schedule("today")
    lines = [s["date"]] + [f"{i['start']}-{i['end']} {i['what']}" for i in s["items"]]
    if not s["items"]:
        lines.append("Nothing on the calendar.")
    if s["free_time"]:
        lines.append("\nFree: " + ", ".join(s["free_time"]))
    await update.message.reply_text("\n".join(lines))


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    rows = planner_tools.get_tasks(7)["tasks"]
    if not rows:
        await update.message.reply_text("Nothing due in the next 7 days.")
        return
    lines = [("OVERDUE " if r["overdue"] else "") + f"{r['task']}: {r['due']}" for r in rows]
    await update.message.reply_text("Due this week:\n" + "\n".join(lines))


async def brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    await update.message.reply_text(briefing.morning_text())


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    get_assistant().reset(str(update.effective_chat.id))
    await update.message.reply_text("Fresh start.")


# ---------- Free-form questions ----------

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        answer = await asyncio.to_thread(get_assistant().reply, str(update.effective_chat.id), update.message.text)
    except Exception as e:  # missing API key, network, etc.
        log.exception("assistant failed")
        answer = f"Something went wrong talking to Claude: {type(e).__name__}: {e}"
    await update.message.reply_text(answer[:4000])


# ---------- Follow-up buttons ----------

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(update):
        await query.answer()
        return
    answer, session_id = query.data.split(":")
    reply = await asyncio.to_thread(briefing.answer_followup, int(session_id), answer)
    await query.answer()
    await query.edit_message_reply_markup(None)
    await query.message.reply_text(reply)
    get_assistant().note(CHAT_ID, reply)


def followup_buttons(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes", callback_data=f"yes:{session_id}"),
        InlineKeyboardButton("Finished the task", callback_data=f"finished:{session_id}"),
        InlineKeyboardButton("Missed it", callback_data=f"no:{session_id}"),
    ]])


# ---------- Background checks (every 5 minutes) ----------

async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHAT_ID:
        return
    now = datetime.now()

    # Keep Google Calendar, Gmail and Blackboard fresh
    if sync.due_for_auto_sync(now):
        results = await asyncio.to_thread(sync.sync_all, now)
        for name, ok, message in results:
            log.info("sync %s: %s", name, message)

    # Morning briefing, once a day
    prefs = db.get_preferences()
    briefing_at = datetime.combine(now.date(), prefs.briefing_time)
    last_sent, _ = db.get_sync_state("briefing")
    if prefs.briefing_on and briefing_at <= now <= briefing_at + BRIEFING_GRACE and (last_sent is None or last_sent.date() != now.date()):
        text = await asyncio.to_thread(briefing.morning_text, now)
        await context.bot.send_message(CHAT_ID, text)
        get_assistant().note(CHAT_ID, text)
        db.set_sync_state("briefing", "sent")

    # "Did you do it?" after study sessions
    for s in db.sessions_needing_followup(now):
        question = briefing.followup_question(s.id)
        if question:
            await context.bot.send_message(CHAT_ID, question, reply_markup=followup_buttons(s.id))
        db.mark_followup_sent(s.id)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("today", "Today's schedule"),
        BotCommand("tasks", "What's due this week"),
        BotCommand("brief", "Morning briefing now"),
        BotCommand("reset", "Start a fresh conversation"),
    ])


def main() -> None:
    if not TOKEN:
        raise SystemExit("Put TELEGRAM_BOT_TOKEN in the .env file first (see .env.example).")
    db.init_db()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("tasks", tasks))
    app.add_handler(CommandHandler("brief", brief))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.job_queue.run_repeating(tick, interval=CHECK_EVERY_SECONDS, first=15)
    log.info("AutoPlan bot running%s", "" if CHAT_ID else " (no TELEGRAM_CHAT_ID yet: send /start to get it)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
