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

def classify(sender: str, subject: str, body: str, due: datetime | None) -> str | None:
    """Return 'blackboard', 'professor', 'time-sensitive', or None to skip."""
    name, address = parseaddr(sender)
    address = address.lower()
    text = f"{subject}\n{body}"

    # Check the raw sender: Blackboard's display name contains an @ that confuses address parsing
    if "blackboard" in sender.lower() or re.search(r"blackboard", subject, re.I):
        return None if BLACKBOARD_NOISE.search(clean_subject(subject)) else "blackboard"

    has_word = bool(DEADLINE_WORDS.search(text))
    if address.endswith("@gwu.edu") and not MASS_MAIL_SENDERS.search(address):
        return "professor" if (has_word or due) else None

    if has_word and due and not MARKETING.search(text):
        return "time-sensitive"
    return None


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


def scan(service, now: datetime, since: datetime | None = None) -> str:
    """
    Look through recent mail and add anything time-sensitive to the review list.
    The first scan covers the last GMAIL_DAYS_BACK days; later scans only
    look at mail since the previous scan (with an hour of overlap).
    """
    profile = service.users().getProfile(userId="me").execute()
    email = profile.get("emailAddress", "")
    if since:
        window = f"after:{int((since - timedelta(hours=1)).replace(tzinfo=ZoneInfo(config.TIMEZONE)).timestamp())}"
    else:
        window = f"newer_than:{config.GMAIL_DAYS_BACK}d"
    query = f"{window} -category:promotions -category:social -in:chats"

    ids, token = [], None
    while True:
        resp = service.users().messages().list(userId="me", q=query, maxResults=100, pageToken=token).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token or len(ids) >= config.GMAIL_MAX_MESSAGES:
            break

    new = 0
    for msg_id in ids[: config.GMAIL_MAX_MESSAGES]:
        msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        sender, subject = _header(msg, "From"), _header(msg, "Subject") or "(no subject)"
        try:
            received = parsedate_to_datetime(_header(msg, "Date")).astimezone(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)
        except (TypeError, ValueError):
            received = now
        body = _body_text(msg.get("payload", {}))
        due = extract_due(f"{subject}\n{body}", received)
        kind = classify(sender, subject, body, due)
        if not kind:
            continue
        item = InboxItem(
            external_id=msg_id,
            subject=clean_subject(subject)[:300],
            sender=sender[:200],
            received_at=received,
            snippet=unescape(msg.get("snippet", ""))[:400],
            kind=kind,
            detected_due=due,
            link=f"https://mail.google.com/mail/?authuser={email}#all/{msg_id}",
        )
        if db.add_inbox_item(item):
            new += 1
    return f"checked {len(ids[: config.GMAIL_MAX_MESSAGES])} emails, {new} new to review"
