# AutoPlan

A local-first personal scheduling assistant. Add your fixed commitments and
assignments, and AutoPlan suggests when to work on each task.

Built with Python, Streamlit, and SQLite. Everything runs on your machine.

## Setup (one time)

From this folder in a terminal (PowerShell or the VS Code terminal):

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On Mac/Linux, activate with `source .venv/bin/activate` instead.

## Run the app

```powershell
.venv\Scripts\activate
streamlit run app.py
```

It opens in your browser at http://localhost:8501. The database is created
automatically at `data/autoplan.db` the first time it runs.

## Open it on your phone (same Wi-Fi)

When the app starts, the terminal prints a **Network URL** like
`http://192.168.1.23:8501`. Open that on your phone while it's on the same
Wi-Fi as your PC. If Windows asks about the firewall, allow Python on
private networks. Your PC has to be on with the app running.

## Connect Google Calendar, Gmail and Blackboard

Everything lives on the **Sync** page. Syncing also runs on its own when you
open the app if the last sync was more than 30 minutes ago.

**Google (Calendar + Gmail), one time:**
1. At console.cloud.google.com, create a project called AutoPlan.
2. Enable **Google Calendar API** and **Gmail API**.
3. Set up the OAuth consent screen as **External** and add your @gwu.edu
   address as a test user.
4. Create an OAuth client of type **Desktop app**, download it, and save it
   as `credentials.json` in this folder (it's gitignored).
5. On the Sync page click **Sign in with Google**, then pick which calendars
   to import.

- Imported calendar events are read-only here. Edit them in Google.
- Accepted study sessions are copied to a separate **AutoPlan** calendar
  with a 10-minute reminder, so your phone alerts you.
- Gmail access is read-only. Blackboard notifications, professor emails and
  anything with a clear deadline land on a review list; nothing becomes a
  task until you approve it.

**Blackboard:** in Blackboard's Calendar, copy the external calendar link
and paste it on the Sync page. Assignments come in as tasks with a priority and
estimate guessed from the name (`autoplan/task_rules.py`); your edits survive re-syncs.

Tweak sync timing and how far back email is scanned in `autoplan/config.py`.

## Blackboard details, Prep, and grades

One-time setup on the computer that runs AutoPlan:
```
pip install -r requirements.txt
playwright install chromium
```
Then Sync → Blackboard → **Connect Blackboard**. A browser opens; sign in with
GW login and Duo. AutoPlan keeps that login in `data/bb_profile` and reads
your current courses every few hours: assignment instructions and files
(saved in `data/bb_files`) and your gradebook. When the login expires, the
Telegram bot tells you to reconnect.

- **Tasks → Details and prep**: instructions, files, and a **Prep** button.
  Claude reads the assignment and returns requirements, materials, steps with
  times, and concepts to review. It plans; it doesn't do the assignment.
- **Grades**: points-based standing per class and what you need on the rest
  for a target letter. Edit the scale in `autoplan/grades.py`.
- The bot answers "what do I need for Assignment 6?" and "what's my grade in
  ISTM 4205?"

If something doesn't come through right, Sync → Blackboard → Troubleshooting →
**Save sample data** writes Blackboard's raw responses to `data/bb_debug`.

## Text your planner on Telegram

1. Copy `.env.example` to `.env` and add your Claude API key and a bot token
   from Telegram's @BotFather.
2. Run `python telegram_bot.py`, open your bot in Telegram, and send `/start`.
   It replies with your chat id; put it in `.env` as `TELEGRAM_CHAT_ID` and
   restart the bot. It ignores everyone else.
3. Ask things like "when should I go to the gym today?" or "anything due for
   ISTM 4205 tomorrow, and when should I start?"

The bot also sends a morning briefing, asks after each study session whether
you did it (missed ones get rescheduled), and keeps Google and Blackboard
synced. Gym days and the briefing time are on the Settings page.

`start_autoplan.bat` starts the app and the bot together. Put a shortcut to it
in `shell:startup` on an always-on computer.

## Run the tests

```powershell
pytest
```

## Where things live

| File | What it does | Edit it to... |
|---|---|---|
| `app.py` | Starts the app and builds the sidebar | Add or reorder pages |
| `views/` | One file per page: dashboard, events, tasks, settings | Change what a page shows |
| `autoplan/scheduler.py` | Scheduling rules, no UI or database code | Tweak how sessions get suggested |
| `autoplan/db.py` | All SQLite reads and writes | Add fields or queries |
| `autoplan/models.py` | Event, Task, StudySession, Preferences | Add fields to a data type |
| `autoplan/week_view.py` | Builds the weekly grid and its colors | Change colors, slot size, layout |
| `autoplan/ui.py` | Shared page helpers (formatting, week picker) | Change how dates and times display |
| `autoplan/integrations/` | Google Calendar, Gmail, Blackboard sync | Change what gets imported or how emails are filtered |
| `autoplan/planner_tools.py` | What the Telegram assistant can look up or change | Give the bot new abilities |
| `autoplan/assistant.py` | The Claude conversation behind the bot | Change its tone or rules |
| `autoplan/briefing.py` | Morning text and study follow-ups | Change what the morning text says |
| `telegram_bot.py` | The Telegram bot itself | Add commands |
| `autoplan/integrations/bb_scraper.py` | Blackboard login, reading courses, assignments, grades | Change what gets read |
| `autoplan/prep.py` | The Prep button's instructions to Claude | Change what Prep gives you |
| `autoplan/grades.py` | Grade math and letter scale | Match a syllabus's scale |
| `autoplan/config.py` | DB path, categories, default preferences | Change defaults for a new database |
| `tests/` | pytest tests | Add scenarios |

## Design notes

- **Soft deletes.** Deleting an event or task marks it hidden instead of
  removing it, so nothing is lost by accident.
- **Recurring events** are stored once ("weekly on Mon/Wed until Dec 12")
  and expanded into actual dates only when needed.
- **Study hours** default to all day (00:00 to 23:59) for a new database and can be set per
  weekday.
- **Weekly view** starts as a simple pandas grid. It can be swapped for the
  `streamlit-calendar` component later if it feels too plain.
- **Times** are local. Timezone handling waits for Google Calendar sync.
- Your data lives in `data/`, which is gitignored.

## Roadmap

### ~~Phase 1: Project setup~~
Repo, folder structure, SQLite, Streamlit skeleton, README.

### ~~Phase 2: Manual scheduling~~
Add, edit, soft-delete and restore events and tasks. Weekly recurrence,
overlap check, color-coded weekly grid, dashboard.

### ~~Phase 3: Scheduling engine + editable settings~~
- Find free time inside working hours, respecting the buffer around events
- Rank open tasks by deadline urgency and priority
- Split large tasks into sessions between the min and max length
- Show suggested sessions on the dashboard to accept or reject
- Accepted sessions appear on the calendar and count as busy time
- Warn when there isn't enough free time before a deadline
- Settings page becomes editable: working hours per weekday, min/max
  session length, buffer
- Tests: overlaps, approaching deadlines, not enough time, multi-session
  tasks, recurring events, completed tasks, scheduling across days

### ~~Phase 4: Integrations~~
- Google Calendar sync both ways (events in, study sessions out with phone reminders)
- Gmail scan for Blackboard notifications, professor announcements and other
  time-sensitive mail, with a review list
- Blackboard assignments imported as tasks from the calendar feed

### Phase 5: Assistant and smarter planning (in progress)
- ~~Telegram planner with Claude, morning text, study-session follow-ups~~
- ~~Gym preferences~~
- ~~Exam mode: review sessions spread over the week before an exam~~
- Robinhood holdings in the dashboard and bot (read-only)
- ~~Blackboard scraper: assignment details, a Prep button, and a grade tracker~~

### Later
- Tailscale so the dashboard works on your phone away from home
- Workload analytics
- Scheduling based on your past productivity
