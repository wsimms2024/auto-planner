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
import html
import logging
import os
from datetime import datetime, timedelta

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from autoplan import briefing, config, db, planner_tools
from autoplan.assistant import Assistant
from autoplan.integrations import sync
from autoplan.workout import actions as workout
from autoplan.workout import settings as workout_settings

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

# ---------- Workout digest ----------

def digest_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Start Workout", callback_data="wk:start"), InlineKeyboardButton("😴 Recovery Check", callback_data="wk:recovery")],
        [InlineKeyboardButton("📈 Progress", callback_data="wk:progress"), InlineKeyboardButton("🍎 Fueling", callback_data="wk:fuel")],
        [InlineKeyboardButton("🔄 Change Split Day", callback_data="wk:split")],
    ])


def split_buttons() -> InlineKeyboardMarkup:
    days = list(dict.fromkeys(workout_settings.SPLIT))
    return InlineKeyboardMarkup([[InlineKeyboardButton(d, callback_data=f"wk:setday:{d}") for d in days[i:i + 3]] for i in range(0, len(days), 3)])


async def send_digest(bot, chat_id) -> None:
    messages = await asyncio.to_thread(workout.digest_messages)
    for i, text in enumerate(messages):
        last = i == len(messages) - 1
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=digest_buttons() if last else None)


async def safe_reply(update: Update, fn, *args) -> None:
    """Run a workout action and reply; show the format help if the input couldn't be read."""
    try:
        text = await asyncio.to_thread(fn, *args)
    except ValueError as e:
        text = html.escape(str(e), quote=False)
    await update.message.reply_text(text, parse_mode="HTML")


async def digest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_owner(update):
        await send_digest(context.bot, update.effective_chat.id)


async def recovery_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    args = " ".join(context.args)
    if not args:
        await update.message.reply_text(workout.recovery_prompt(), parse_mode="HTML")
        return
    await safe_reply(update, workout.handle_recovery, args)


async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    args = update.message.text.partition(" ")[2]
    if not args.strip():
        await update.message.reply_text("📝 <code>/log push; bench 240 4x4 @8; incline db 70 3x10 @9</code>\n"
                                        "Reps per set also work: <code>bench 245 4,4,4,3 @8,8,8.5,9.5</code>", parse_mode="HTML")
        return
    await safe_reply(update, workout.handle_log, args)


async def fuel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_owner(update):
        await safe_reply(update, workout.handle_fuel, " ".join(context.args))


async def progress_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_owner(update):
        await safe_reply(update, workout.progress_text)


async def weekly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_owner(update):
        await safe_reply(update, workout.weekly_text)


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_owner(update):
        await safe_reply(update, workout.settings_text)


async def workout_button(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    action = query.data.split(":", 2)
    chat = query.message.chat_id
    if action[1] == "start":
        await query.message.reply_text(await asyncio.to_thread(workout.start_workout_text), parse_mode="HTML")
    elif action[1] == "recovery":
        await query.message.reply_text(workout.recovery_prompt(), parse_mode="HTML")
    elif action[1] == "progress":
        await query.message.reply_text(await asyncio.to_thread(workout.progress_text), parse_mode="HTML")
    elif action[1] == "fuel":
        await query.message.reply_text(await asyncio.to_thread(workout.handle_fuel, ""), parse_mode="HTML")
    elif action[1] == "split":
        await query.message.reply_text("Which day is today?", reply_markup=split_buttons())
    elif action[1] == "setday":
        await query.message.reply_text(workout.set_split(action[2]), parse_mode="HTML")
        await send_digest(context.bot, chat)


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(update):
        await query.answer()
        return
    if query.data.startswith("wk:"):
        await query.answer()
        await workout_button(query, context)
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

    # Blackboard login expired: say so once
    if sync.blackboard_needs_login():
        failed_at, _ = db.get_sync_state("blackboard_details")
        told_at, _ = db.get_sync_state("bb_login_notice")
        if told_at is None or told_at < failed_at:
            await context.bot.send_message(CHAT_ID, "Your Blackboard login expired. Open AutoPlan → Sync → Blackboard and click Connect Blackboard.")
            db.set_sync_state("bb_login_notice", "sent")

    # Morning briefing, once a day
    prefs = db.get_preferences()
    briefing_at = datetime.combine(now.date(), prefs.briefing_time)
    last_sent, _ = db.get_sync_state("briefing")
    if prefs.briefing_on and briefing_at <= now <= briefing_at + BRIEFING_GRACE and (last_sent is None or last_sent.date() != now.date()):
        text = await asyncio.to_thread(briefing.morning_text, now)
        await context.bot.send_message(CHAT_ID, text)
        get_assistant().note(CHAT_ID, text)
        db.set_sync_state("briefing", "sent")
        if db.get_preference_value("workout_digest_on") is not False:
            await send_digest(context.bot, CHAT_ID)

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
        BotCommand("digest", "Today's workout"),
        BotCommand("recovery", "Recovery check-in"),
        BotCommand("log", "Log a workout"),
        BotCommand("fuel", "What you ate before the gym"),
        BotCommand("progress", "Strength progress"),
        BotCommand("weekly", "Last 7 days of training"),
        BotCommand("settings", "Training settings"),
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
    app.add_handler(CommandHandler("digest", digest_cmd))
    app.add_handler(CommandHandler("recovery", recovery_cmd))
    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CommandHandler("fuel", fuel_cmd))
    app.add_handler(CommandHandler("progress", progress_cmd))
    app.add_handler(CommandHandler("weekly", weekly_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.job_queue.run_repeating(tick, interval=CHECK_EVERY_SECONDS, first=15)
    log.info("AutoPlan bot running%s", "" if CHAT_ID else " (no TELEGRAM_CHAT_ID yet: send /start to get it)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
