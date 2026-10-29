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
| `autoplan/config.py` | DB path, categories, default preferences | Change defaults for a new database |
| `tests/` | pytest tests | Add scenarios |

## Design notes

- **Soft deletes.** Deleting an event or task marks it hidden instead of
  removing it, so nothing is lost by accident.
- **Recurring events** are stored once ("weekly on Mon/Wed until Dec 12")
  and expanded into actual dates only when needed.
- **Working hours** default to 9am to 8pm every day and can be set per
  weekday.
- **Weekly view** starts as a simple pandas grid. It can be swapped for the
  `streamlit-calendar` component later if it feels too plain.
- **Times** are local. Timezone handling waits for Google Calendar sync.
- Your data lives in `data/`, which is gitignored.

## Roadmap

1. ~~Project setup~~
2. ~~Manual scheduling: add/edit/delete events and tasks, weekly view~~
3. Scheduling engine: free time, priorities, suggested sessions
4. Usability: editable settings, validation, more tests
5. Integrations (after MVP): Google Calendar, Blackboard scraper
