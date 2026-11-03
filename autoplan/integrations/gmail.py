"""
Scan Gmail for time-sensitive mail and put it on the review list.

Rule-based, no AI. A message is kept if it's one of:
- blackboard:     a Blackboard notification (any of them)
- professor:      from a @gwu.edu person (not a mass mailer) that mentions a
                  deadline-type word or a date
- time-sensitive: from anyone else, but only with a deadline-type word AND a
                  specific upcoming date, and it isn't marketing

Nothing is added as a task automatically. You approve each one on the Sync
page. Gmail access is read-only.
"""

import base64
import re
import time as clock
from datetime import datetime, time, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

from autoplan import config, db
from autoplan.models import InboxItem

SOURCE = "gmail"

DEADLINE_WORDS = re.compile(
    r"\b(due|deadline|submit|submission|exam|midterm|final|quiz|test|"
    r"reschedul\w*|postpone\w*|cancel\w*|moved to|no class|class is|"
    r"last day|registration|register by|expires?|action required|urgent|reminder|"
    r"assignment|homework|hw\d*|project|paper|presentation|office hours|interview)\b",
    re.IGNORECASE,
)
# Words that point straight at the due date ("due Nov 5", "submit by Friday")
STRONG_DUE_WORDS = re.compile(r"\b(due|deadline|by|before|no later than|submit\w*|moved to|reschedul\w*)\b", re.IGNORECASE)
MASS_MAIL_SENDERS = re.compile(
    r"(no-?reply|newsletter|gwtoday|gw today|announcements?@|marketing|events@|digest|mailer|notifications?@(?!.*blackboard))",
    re.IGNORECASE,
)
# Blackboard emails that are just receipts, not anything you need to act on
BLACKBOARD_NOISE = re.compile(r"^(submission received|.*grade (?:has been )?posted)", re.IGNORECASE)
# Display names that belong to an office or program, not a person
OFFICE_NAME = re.compile(
    r"\b(center|centre|office|living|services?|career|life|infomail|alert|department|dept|school|program|team|"
    r"university|communications?|education|engagement|registrar|admissions|library|athletics|news|events?|"
    r"council|association|club|society|division|institute|college|housing|dining|it support|gw\w*)\b",
    re.IGNORECASE,
)

# Subjects that are about a deadline even when the date isn't right next to "due"
URGENT_SUBJECT = re.compile(
    r"\b(due|deadline|registration|register|last day|reminder|action required|closes|closing|expires?|"
    r"final notice|urgent|cancel\w*|reschedul\w*|postpone\w*|exam|quiz|midterm|interview)\b",
    re.IGNORECASE,
)
MARKETING = re.compile(r"\b(unsubscribe|% off|sale ends|shop now|promo code|limited time)\b", re.IGNORECASE)

MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
TIME = r"(?:\s*(?:at|by|@|,)?\s*(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?|noon|midnight))?"
DATE_PATTERNS = [
    re.compile(rf"\b({MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?){TIME}", re.IGNORECASE),
    re.compile(rf"\b(\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?){TIME}", re.IGNORECASE),
    re.compile(rf"\b(today|tonight|tomorrow|(?:this |next )?(?:{'|'.join(WEEKDAYS)})){TIME}", re.IGNORECASE),
]


# ---------- Pure logic (easy to test) ----------

def classify(sender: str, subject: str, body: str, due: datetime | None, strong_date: bool = False, bulk: bool = False) -> str | None:
    """
    Return 'blackboard', 'professor', 'time-sensitive', or None to skip.

    strong_date: the due date sits right after "due", "by", "deadline" etc.
    bulk: the email is a mailing-list blast (has List-Unsubscribe / List-Id headers).
    """
    name, address = parseaddr(sender)
    address = address.lower()
    text = f"{subject}\n{body}"
    if address in muted_senders():
        return None
    bulk = bulk or bool(OFFICE_NAME.search(name))

    # Check the raw sender: Blackboard's display name contains an @ that confuses address parsing
    if "blackboard" in sender.lower() or re.search(r"blackboard", subject, re.I):
        return None if BLACKBOARD_NOISE.search(clean_subject(subject)) else "blackboard"

    if MARKETING.search(text) and not address.endswith("@gwu.edu"):
        return None

    # A real person at GW writing to you (not a newsletter or office blast)
    if address.endswith("@gwu.edu") and not bulk and not MASS_MAIL_SENDERS.search(address):
        return "professor" if (DEADLINE_WORDS.search(text) or due) else None

    # Everyone else, including GW office blasts: needs a clear deadline
    if due and (strong_date or URGENT_SUBJECT.search(subject)):
        return "time-sensitive"
    return None


def muted_senders() -> set[str]:
    """Addresses you've muted on the Sync page."""
    return set(db.get_preference_value("gmail_muted_senders") or [])


def sender_address(sender: str) -> str:
    return parseaddr(sender)[1].lower()


def clean_subject(subject: str) -> str:
    """
    Shorten Blackboard's subject lines:
    '54792_202603 (202603_Web Applications Development_ISTM_4205_10): Assignment 4'
    -> 'ISTM 4205: Assignment 4'
    """
    m = re.match(r"^\S+_\d+\s+\((.+?)\):\s*(.+)$", subject)
    if not m:
        return subject
    course, title = m.groups()
    code = re.search(r"_([A-Z]{2,4})_(\d{4})_", course)
    label = f"{code.group(1)} {code.group(2)}" if code else re.sub(r"^\d+_", "", course)
    title = re.sub(r"^Announcement:\s*", "", title)
    return f"{label}: {title}"


def _parse_time(raw: str | None) -> time:
    if not raw:
        return time(23, 59)
    raw = raw.lower().replace(".", "").strip()
    if raw == "noon":
        return time(12, 0)
    if raw == "midnight":
        return time(23, 59)
    return dateparser.parse(raw).time()


def _resolve(date_text: str, received: datetime) -> datetime | None:
    t = date_text.lower().strip()
    if t in ("today", "tonight"):
        return received
    if t == "tomorrow":
        return received + timedelta(days=1)
    for i, day in enumerate(WEEKDAYS):
        if t.endswith(day):
            ahead = (i - received.weekday()) % 7 or 7
            if t.startswith("next "):
                ahead += 7 if ahead < 7 else 0
            if t.startswith("this ") and ahead == 7:
                ahead = 0
            return received + timedelta(days=ahead)
    try:
        dt = dateparser.parse(re.sub(r"(st|nd|rd|th)\b", "", t), default=received.replace(hour=0, minute=0))
    except (ValueError, OverflowError):
        return None
    # "Jan 5" written in December means next January
    if dt.date() < received.date() - timedelta(days=60) and not re.search(r"\d{4}", t):
        dt = dt.replace(year=dt.year + 1)
    return dt


def extract_due(text: str, received: datetime) -> datetime | None:
    """
    Find the date something is due. A date right after "due", "by",
    "deadline" etc. wins; then a date near any deadline-type word; then the
    first upcoming date. Words like "today" or "Friday" only count when they
    come with a time or right after a deadline word, so "great class today"
    isn't a deadline. Dates with no time get 11:59 PM. Past dates are ignored.
    """
    candidates = []
    for index, pattern in enumerate(DATE_PATTERNS):
        is_relative = index == 2
        for m in pattern.finditer(text):
            before = text[max(0, m.start() - 40):m.start()]
            strong = bool(STRONG_DUE_WORDS.search(before[-20:]))
            near = bool(DEADLINE_WORDS.search(before))
            if is_relative and not (strong or near or m.group(2)):
                continue
            day = _resolve(m.group(1), received)
            if day is None:
                continue
            due = datetime.combine(day.date(), _parse_time(m.group(2)))
            if due < received - timedelta(hours=1) or due > received + timedelta(days=180):
                continue
            rank = 0 if strong else 1 if near else 2
            candidates.append((rank, m.start(), due))
    return min(candidates)[2] if candidates else None


def find_due(text: str, received: datetime) -> tuple[datetime | None, bool]:
    """extract_due plus whether the date came right after a word like 'due' or 'by'."""
    due = extract_due(text, received)
    if due is None:
        return None, False
    strong = any(
        STRONG_DUE_WORDS.search(text[max(0, m.start() - 20):m.start()])
        for pattern in DATE_PATTERNS for m in pattern.finditer(text)
        if (d := _resolve(m.group(1), received)) and d.date() == due.date()
    )
    return due, strong


# ---------- Gmail API ----------

def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _body_text(payload: dict) -> str:
    """Plain text of a message, falling back to HTML with tags stripped."""
    plain, html = [], []

    def walk(part):
        data = part.get("body", {}).get("data")
        mime = part.get("mimeType", "")
        if data:
            text = base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
            (plain if mime == "text/plain" else html if mime == "text/html" else plain).append(text)
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain)
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", "\n".join(html), flags=re.S | re.I)
    return unescape(re.sub(r"<[^>]+>", " ", text))


# Gmail's own search narrows things down first, so only likely matches get
# downloaded. The rules above then decide what actually reaches the review list.
CANDIDATE_QUERY = (
    "from:blackboard.com OR from:gwu.edu OR due OR deadline OR exam OR midterm OR quiz "
    "OR rescheduled OR postponed OR cancelled OR canceled OR interview OR \"action required\" OR urgent OR submit"
)


def _is_rate_limit(error: Exception) -> bool:
    return "rateLimitExceeded" in str(error) or "Quota exceeded" in str(error) or " 429 " in str(error)


def scan(service, now: datetime) -> str:
    """
    Check recent mail and add anything time-sensitive to the review list.

    - Covers the last GMAIL_DAYS_BACK days every time, but each email is only
      downloaded once (remembered in the gmail_seen table).
    - At most GMAIL_MAX_MESSAGES new emails per run, paced to stay under
      Gmail's per-minute limit. If the limit is hit anyway, it stops, keeps
      what it found, and picks up where it left off next sync.
    """
    profile = service.users().getProfile(userId="me").execute(num_retries=3)
    email = profile.get("emailAddress", "")
    query = f"newer_than:{config.GMAIL_DAYS_BACK}d -category:promotions -category:social -in:chats ({CANDIDATE_QUERY})"

    ids, token = [], None
    while True:
        resp = service.users().messages().list(userId="me", q=query, maxResults=100, pageToken=token).execute(num_retries=3)
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token or len(ids) >= 500:
            break

    todo = db.filter_unseen_emails(ids)
    batch = todo[: config.GMAIL_MAX_MESSAGES]
    checked = new = 0
    paused = False
    for msg_id in batch:
        try:
            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute(num_retries=3)
        except Exception as e:
            if _is_rate_limit(e):
                paused = True
                break
            raise
        checked += 1
        if _process(msg, email, now):
            new += 1
        db.mark_email_seen(msg_id)
        clock.sleep(config.GMAIL_PAUSE_SECONDS)

    left = len(todo) - checked
    message = f"checked {checked} emails, {new} new to review"
    if paused:
        message += f"; hit Gmail's rate limit, {left} left for next sync"
    elif left:
        message += f"; {left} more next sync"
    return message


def _process(msg: dict, email: str, now: datetime) -> bool:
    """Classify one message and add it to the review list. True if it was added."""
    sender, subject = _header(msg, "From"), _header(msg, "Subject") or "(no subject)"
    try:
        received = parsedate_to_datetime(_header(msg, "Date")).astimezone(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)
    except (TypeError, ValueError):
        received = now
    body = _body_text(msg.get("payload", {}))
    due, strong = find_due(f"{subject}\n{body}", received)
    bulk = bool(_header(msg, "List-Unsubscribe") or _header(msg, "List-Id") or _header(msg, "Precedence").lower() in ("bulk", "list"))
    kind = classify(sender, subject, body, due, strong, bulk)
    if not kind:
        return False
    item = InboxItem(
        external_id=msg["id"],
        subject=clean_subject(subject)[:300],
        sender=sender[:200],
        received_at=received,
        snippet=unescape(msg.get("snippet", ""))[:400],
        kind=kind,
        detected_due=due,
        link=f"https://mail.google.com/mail/?authuser={email}#all/{msg['id']}",
    )
    return db.add_inbox_item(item)


def recheck_pending(service, now: datetime) -> str:
    """Re-run the current rules on everything waiting for review; dismiss what no longer qualifies."""
    removed = 0
    for item in db.list_inbox():
        try:
            msg = service.users().messages().get(userId="me", id=item.external_id, format="full").execute(num_retries=3)
        except Exception as e:
            if _is_rate_limit(e):
                break
            continue
        sender, subject = _header(msg, "From"), _header(msg, "Subject") or ""
        body = _body_text(msg.get("payload", {}))
        due, strong = find_due(f"{subject}\n{body}", item.received_at)
        bulk = bool(_header(msg, "List-Unsubscribe") or _header(msg, "List-Id") or _header(msg, "Precedence").lower() in ("bulk", "list"))
        if not classify(sender, subject, body, due, strong, bulk):
            db.set_inbox_status(item.id, "dismissed")
            removed += 1
        clock.sleep(config.GMAIL_PAUSE_SECONDS)
    return f"removed {removed} that don't match the current rules"
