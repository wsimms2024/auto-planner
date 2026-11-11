"""
Blackboard scraper: assignment details, files and grades.

How it works:
1. "Connect Blackboard" opens a real browser. You sign in (GW login + Duo).
   The login is kept in data/bb_profile, like a browser remembering you.
2. Later runs reuse that login in the background and read Blackboard's own
   data API (the same one the Blackboard website uses), so no page clicking.
3. When the login expires, syncs report "needs login" and the bot tells you.

Only current-term courses are read. Nothing is ever submitted or changed.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

from autoplan import config, db

PROFILE_DIR = config.PROJECT_ROOT / "data" / "bb_profile"
STATE_PATH = config.PROJECT_ROOT / "data" / "bb_session.json"  # saved cookies (incl. session cookies)
FILES_DIR = config.PROJECT_ROOT / "data" / "bb_files"
DEBUG_DIR = config.PROJECT_ROOT / "data" / "bb_debug"
API = "/learn/api/public"

# Content types that are things you hand in or take
GRADABLE_HANDLERS = ("resource/x-bb-assignment", "resource/x-bb-asmt-test-link", "resource/x-bb-externallink-graded")


class NeedsLogin(Exception):
    """The saved Blackboard login is missing or expired."""


def has_login() -> bool:
    return STATE_PATH.exists() or (PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir()))


# The background browser reports itself as normal Chrome; some sign-in systems
# turn away "HeadlessChrome".
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def _signed_in(ctx) -> bool:
    r = ctx.request.get(config.BLACKBOARD_URL + f"{API}/v1/users/me", fail_on_status_code=False)
    return r.ok and r.headers.get("content-type", "").startswith("application/json")


def _save_state(ctx) -> None:
    """Save every cookie, including session-only ones the browser would otherwise drop on close."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage_state(path=str(STATE_PATH))


def _load_state(ctx) -> None:
    if STATE_PATH.exists():
        cookies = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("cookies", [])
        if cookies:
            ctx.add_cookies(cookies)


# ---------- Login (opens a visible browser once) ----------

def login_interactive(timeout_seconds: int = 300) -> bool:
    """Open Blackboard in a visible browser and wait until you're signed in."""
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False, user_agent=USER_AGENT)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.BLACKBOARD_URL + "/ultra/course")
        deadline = datetime.now() + timedelta(seconds=timeout_seconds)
        try:
            while datetime.now() < deadline:
                page.wait_for_timeout(2000)
                if _signed_in(ctx):
                    page.wait_for_timeout(1500)  # let the cookies settle
                    _save_state(ctx)
                    return True
            return False
        finally:
            ctx.close()


# ---------- Reading data with the saved login ----------

class BBClient:
    """Background (headless) browser session used only to call Blackboard's JSON API."""

    def __enter__(self):
        if not has_login():
            raise NeedsLogin("Connect Blackboard on the Sync page first.")
        from playwright.sync_api import sync_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(str(PROFILE_DIR), headless=True, user_agent=USER_AGENT)
        _load_state(self.ctx)
        if not _signed_in(self.ctx) and not self._quiet_relogin():
            detail = self._describe_me()
            self.__exit__()
            raise NeedsLogin(f"Your Blackboard login expired. Connect Blackboard again on the Sync page. ({detail})")
        return self

    def __exit__(self, *exc):
        try:
            if _signed_in(self.ctx):
                _save_state(self.ctx)  # keep refreshed cookies for next time
        except Exception:
            pass
        self.ctx.close()
        self._pw.stop()

    def _quiet_relogin(self) -> bool:
        """Open Blackboard in the background; if GW's sign-in still remembers you, this logs back in with no Duo."""
        try:
            page = self.ctx.new_page()
            page.goto(config.BLACKBOARD_URL + "/ultra/course", wait_until="domcontentloaded", timeout=30000)
            for _ in range(10):
                page.wait_for_timeout(1500)
                if _signed_in(self.ctx):
                    _save_state(self.ctx)
                    return True
        except Exception:
            pass
        return False

    def _describe_me(self) -> str:
        """What Blackboard sent back, to make a failed login easy to diagnose."""
        try:
            r = self.ctx.request.get(config.BLACKBOARD_URL + f"{API}/v1/users/me", fail_on_status_code=False, max_redirects=0)
            where = r.headers.get("location", "")
            return f"Blackboard answered {r.status} {r.headers.get('content-type', '').split(';')[0]}" + (f", redirect to {where[:80]}" if where else "")
        except Exception as e:
            return f"{type(e).__name__}"

    def get(self, path: str, **params):
        url = path if path.startswith("http") else config.BLACKBOARD_URL + path
        r = self.ctx.request.get(url, params=params or None, fail_on_status_code=False)
        is_json = r.headers.get("content-type", "").startswith("application/json")
        if r.status in (401, 403) or (r.ok and not is_json):
            raise NeedsLogin(f"Your Blackboard login expired. Connect Blackboard again on the Sync page. "
                             f"(Blackboard answered {r.status} {r.headers.get('content-type', '').split(';')[0]} for {path.split('?')[0][-60:]})")
        if not r.ok:
            return None  # e.g. 404 for a course feature that isn't used
        return r.json()

    def get_all(self, path: str, **params) -> list:
        """Follow Blackboard's paging and return every result."""
        out, page = [], self.get(path, **params)
        while page:
            out += page.get("results", [])
            nxt = (page.get("paging") or {}).get("nextPage")
            page = self.get(nxt) if nxt else None
        return out

    def download(self, path: str, dest: Path) -> bool:
        r = self.ctx.request.get(config.BLACKBOARD_URL + path, fail_on_status_code=False)
        if not r.ok:
            return False
        body = r.body()
        if len(body) > config.BLACKBOARD_MAX_FILE_MB * 1024 * 1024:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return True


# ---------- Parsing (pure functions, tested with sample data) ----------

@dataclass
class Course:
    id: str
    code: str
    name: str
    term: str
    url: str


@dataclass
class Assignment:
    id: str
    course_id: str
    title: str
    due: datetime | None
    instructions: str
    points: float | None = None
    url: str = ""
    attachments: list[dict] = field(default_factory=list)


def to_local(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None) if dt.tzinfo else dt


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", html)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def course_code(course: dict) -> str:
    """'202603_Web Applications Development_ISTM_4205_10' -> 'ISTM 4205'."""
    for value in (course.get("courseId", ""), course.get("name", "")):
        m = re.search(r"(?<![A-Za-z])([A-Z]{2,4})[ _-](\d{4})(?!\d)", value)
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return course.get("name", course.get("courseId", "Course"))[:30]


def clean_name(name: str) -> str:
    """'202603_Web Applications Development_ISTM_4205_10' -> 'Web Applications Development'."""
    name = re.sub(r"^\d+_", "", name)
    name = re.sub(r"_[A-Z]{2,4}_\d{4}(_\w+)?$", "", name)
    return name.replace("_", " ").strip()


def term_of(course: dict) -> str:
    """GW course ids carry the term: '54792_202603' -> '202603'."""
    m = re.search(r"(20\d{4})", course.get("courseId", "") + " " + course.get("externalId", ""))
    return m.group(1) if m else ""


def parse_courses(memberships: list[dict]) -> list[Course]:
    """Current-term courses only (the newest term code among your courses)."""
    courses = []
    for m in memberships:
        c = m.get("course") or {}
        if not c or (c.get("availability") or {}).get("available") == "No":
            continue
        courses.append(Course(
            id=c["id"], code=course_code(c), name=clean_name(c.get("name", "")),
            term=term_of(c), url=c.get("externalAccessUrl", ""),
        ))
    terms = sorted({c.term for c in courses if c.term})
    if terms:
        courses = [c for c in courses if c.term == terms[-1]]
    return courses


def walk_contents(items: list[dict]) -> list[dict]:
    """Flatten a contents tree (folders can hold children)."""
    out = []
    for item in items:
        out.append(item)
        out += walk_contents(item.get("children") or [])
    return out


def parse_assignments(course: Course, contents: list[dict], columns: list[dict]) -> list[Assignment]:
    """Gradable content items, with due date and points from the gradebook when the item lacks them."""
    by_content = {c.get("contentId"): c for c in columns if c.get("contentId")}
    out = []
    for item in contents:
        handler = (item.get("contentHandler") or {}).get("id", "")
        col = by_content.get(item.get("id"))
        if handler not in GRADABLE_HANDLERS and col is None:
            continue
        due = to_local(((item.get("contentHandler") or {}).get("dueDate"))
                       or ((col or {}).get("grading") or {}).get("due"))
        points = ((col or {}).get("score") or {}).get("possible")
        out.append(Assignment(
            id=item["id"], course_id=course.id, title=item.get("title", "Untitled"),
            due=due, instructions=html_to_text(item.get("body") or item.get("description")),
            points=points, url=f"{config.BLACKBOARD_URL}/ultra/courses/{course.id}/outline",
        ))
    return out


def parse_grades(course: Course, columns: list[dict], my_grades: list[dict]) -> list[dict]:
    """One row per gradebook column with your score (None if not graded yet)."""
    grades = {g.get("columnId"): g for g in my_grades}
    rows = []
    for col in columns:
        g = grades.get(col["id"], {})
        score = g.get("score")
        if g.get("exempt") or g.get("status") in ("NeedsGrading",):
            score = None
        display = ((g.get("displayGrade") or {}).get("text")) or None
        rows.append({
            "id": col["id"], "course_id": course.id, "name": col.get("name", ""),
            "possible": (col.get("score") or {}).get("possible"),
            "score": score, "display": display,
            "due": to_local((col.get("grading") or {}).get("due")),
            "is_total": bool(col.get("externalGrade")) or (col.get("name", "").lower() in ("total", "overall grade", "weighted total")),
        })
    return rows


# ---------- Fetch everything ----------

def fetch(client: BBClient, now: datetime) -> dict:
    me = client.get(f"{API}/v1/users/me")
    if not me:
        raise NeedsLogin("Couldn't read your Blackboard profile. Connect Blackboard again.")
    memberships = client.get_all(f"{API}/v1/users/{me['id']}/courses", expand="course", limit=100)
    courses = parse_courses(memberships)

    result = {"courses": courses, "assignments": [], "grades": []}
    for course in courses:
        columns = client.get_all(f"{API}/v2/courses/{course.id}/gradebook/columns", limit=200)
        my_grades = client.get_all(f"{API}/v2/courses/{course.id}/gradebook/users/{me['id']}", limit=200)
        contents = _contents(client, course.id)
        assignments = parse_assignments(course, contents, columns)
        for a in assignments:
            if a.due and now - timedelta(days=1) <= a.due <= now + timedelta(days=config.BLACKBOARD_FILES_DAYS_AHEAD):
                a.attachments = _download_attachments(client, course, a)
        result["assignments"] += assignments
        result["grades"] += parse_grades(course, columns, my_grades)
    return result


def _contents(client: BBClient, course_id: str) -> list[dict]:
    """All content items. Tries one recursive call, falls back to walking folders."""
    items = client.get_all(f"{API}/v1/courses/{course_id}/contents", recursive="true", limit=200)
    if any(i.get("hasChildren") for i in items) and not any(i.get("parentId") for i in items):
        flat, queue = list(items), [i for i in items if i.get("hasChildren")]
        while queue:
            folder = queue.pop()
            kids = client.get_all(f"{API}/v1/courses/{course_id}/contents/{folder['id']}/children", limit=200)
            flat += kids
            queue += [k for k in kids if k.get("hasChildren")]
        return flat
    return walk_contents(items)


def _download_attachments(client: BBClient, course: Course, a: Assignment) -> list[dict]:
    files = []
    for att in client.get_all(f"{API}/v1/courses/{course.id}/contents/{a.id}/attachments") or []:
        name = re.sub(r'[\\/:*?"<>|]', "_", att.get("fileName", "file"))
        dest = FILES_DIR / course.code.replace(" ", "_") / a.id / name
        if dest.exists() or client.download(f"{API}/v1/courses/{course.id}/contents/{a.id}/attachments/{att['id']}/download", dest):
            files.append({"name": name, "path": str(dest)})
    return files


# ---------- Save ----------

def save(data: dict, now: datetime) -> str:
    db.save_bb_courses(data["courses"], now)
    linked = 0
    for a in data["assignments"]:
        a_task = _match_task(a)
        if a_task:
            linked += 1
        db.save_bb_assignment(a, a_task, now)
    db.replace_bb_grades([c.id for c in data["courses"]], data["grades"])
    return (f"{len(data['courses'])} courses, {len(data['assignments'])} assignments "
            f"({linked} matched to tasks), {sum(1 for g in data['grades'] if g['score'] is not None)} grades")


def _match_task(a: Assignment) -> int | None:
    """Find the AutoPlan task for an assignment: same due date (within a minute) and a similar name."""
    import difflib

    if not a.due:
        return None
    best, best_ratio = None, 0.0
    for t in db.list_tasks(include_deleted=True):
        if abs((t.deadline - a.due).total_seconds()) > 60 and t.deadline.date() != a.due.date():
            continue
        x, y = t.name.lower(), a.title.lower()
        ratio = 1.0 if (x in y or y in x) else difflib.SequenceMatcher(None, x, y).ratio()
        if ratio > best_ratio:
            best, best_ratio = t.id, ratio
    return best if best_ratio >= 0.6 else None


def sync(now: datetime | None = None) -> str:
    now = now or datetime.now()
    with BBClient() as client:
        data = fetch(client, now)
    return save(data, now)


# ---------- Troubleshooting ----------

def probe() -> str:
    """Save raw API responses to data/bb_debug so the parsing can be checked against GW's real data."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    with BBClient() as client:
        me = client.get(f"{API}/v1/users/me")
        dump = {"me": {k: me.get(k) for k in ("id", "userName")} if me else None}
        memberships = client.get_all(f"{API}/v1/users/{me['id']}/courses", expand="course", limit=100) if me else []
        dump["memberships"] = memberships
        courses = parse_courses(memberships)
        dump["current_courses"] = [c.__dict__ for c in courses]
        for c in courses[:2]:
            dump[f"{c.code} columns"] = client.get_all(f"{API}/v2/courses/{c.id}/gradebook/columns", limit=200)
            dump[f"{c.code} my grades"] = client.get_all(f"{API}/v2/courses/{c.id}/gradebook/users/{me['id']}", limit=200)
            dump[f"{c.code} contents"] = client.get_all(f"{API}/v1/courses/{c.id}/contents", recursive="true", limit=200)
    path = DEBUG_DIR / f"probe_{datetime.now():%Y%m%d_%H%M}.json"
    path.write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")
    return str(path)
