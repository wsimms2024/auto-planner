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

## Run the tests

```powershell
pytest
```

## Where things live

| File | What it does | Edit it to... |
|---|---|---|
| `app.py` | Dashboard (home page) | Change what the home page shows |
| `pages/` | Events, Tasks, Settings pages (auto-added to the sidebar) | Change those pages |
| `autoplan/scheduler.py` | Scheduling rules, no UI or database code | Tweak how sessions get suggested |
| `autoplan/db.py` | All SQLite reads and writes | Add fields or queries |
| `autoplan/models.py` | Event, Task, StudySession, Preferences | Add fields to a data type |
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
2. Manual scheduling: add/edit/delete events and tasks, weekly view
3. Scheduling engine: free time, priorities, suggested sessions
4. Usability: editable settings, validation, more tests
5. Integrations (after MVP): Google Calendar, Blackboard scraper
