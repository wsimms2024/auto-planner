"""
Ask Claude questions about course documents (syllabi, assignment sheets, slides).

PDFs go to Claude as the actual file, so it sees tables (like a week-by-week
schedule), headers and scanned pages, not just whatever text pypdf manages to
pull out. Word and text files are sent as text. Repeat questions about the same
file within a few minutes reuse Anthropic's prompt cache, so they're cheaper.
"""

import base64
import os
from pathlib import Path

from autoplan import config, db

MAX_PDF_BYTES = 30 * 1024 * 1024  # Anthropic's request limit is 32 MB
MAX_TEXT_CHARS = 150_000

SYSTEM = """You answer Will's questions about his course documents. Use only what the documents say.
Be brief and direct (this goes to a text message): the answer first, then the exact wording if it's a policy,
grade weight, date or deadline, with the page number when you can see it.
If the documents don't answer it, say that plainly and don't guess. No em dashes."""


class NoDocuments(LookupError):
    pass


def syllabus_files(course: dict) -> list[str]:
    """Downloaded syllabus files for a course; falls back to course content titled or filed under 'syllabus'."""
    paths = [p for p in course.get("syllabus") or [] if Path(p).exists()]
    if paths:
        return paths
    from autoplan.integrations import bb_scraper

    for row in db.search_course_files("syllabus", course["id"], limit=3):
        if "syllabus" in (row["title"] + " " + (row.get("folder") or "")).lower():
            try:
                paths += bb_scraper.download_content(course["id"], row["id"].split(":", 1)[1])
            except Exception:
                continue
    return paths


def content_blocks(paths: list[str]) -> list[dict]:
    """Claude message blocks for the files: PDFs as documents, everything else as text."""
    from autoplan import prep

    blocks = []
    for path in paths:
        p = Path(path)
        if p.suffix.lower() == ".pdf" and p.stat().st_size <= MAX_PDF_BYTES:
            blocks.append({"type": "document", "title": p.name,
                           "source": {"type": "base64", "media_type": "application/pdf",
                                      "data": base64.b64encode(p.read_bytes()).decode()}})
        else:
            text = _full_text(p) or prep.extract_text(path)
            if text.strip():
                blocks.append({"type": "text", "text": f"<document name=\"{p.name}\">\n{text}\n</document>"})
    if blocks:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def _full_text(p: Path) -> str:
    """Like prep.extract_text but without its short cap (questions need the whole file)."""
    try:
        if p.suffix.lower() == ".docx":
            import docx

            d = docx.Document(str(p))
            rows = [" | ".join(c.text for c in r.cells) for t in d.tables for r in t.rows]
            return ("\n".join(par.text for par in d.paragraphs) + "\n" + "\n".join(rows))[:MAX_TEXT_CHARS]
        if p.suffix.lower() == ".pdf":  # too big to send as a file
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(str(p)).pages)[:MAX_TEXT_CHARS]
        if p.suffix.lower() in (".txt", ".md", ".html", ".csv"):
            return p.read_text(encoding="utf-8", errors="replace")[:MAX_TEXT_CHARS]
    except Exception:
        return ""
    return ""


def ask(paths: list[str], question: str, label: str = "", api=None) -> str:
    """Claude's answer to a question about these files."""
    blocks = content_blocks(paths)
    if not blocks:
        raise NoDocuments("Couldn't read any of those files.")
    if api is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("No ANTHROPIC_API_KEY in .env.")
        from autoplan import llm

        api = llm.client()
    prompt = (f"Course: {label}\n" if label else "") + f"Question: {question}"
    try:
        response = _call(api, blocks, prompt)
    except Exception as e:
        # e.g. a PDF over the page limit: retry with extracted text instead of the file
        msg = str(e).lower()
        if not any(b["type"] == "document" for b in blocks) or ("pdf" not in msg and "page" not in msg):
            raise
        text_blocks = [{"type": "text", "text": f"<document name=\"{Path(p).name}\">\n{_full_text(Path(p))}\n</document>"} for p in paths]
        response = _call(api, text_blocks, prompt)
    return "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text").strip()


def _call(api, blocks, prompt):
    return api.messages.create(model=config.CLAUDE_MODEL, max_tokens=1200, system=SYSTEM,
                               messages=[{"role": "user", "content": [*blocks, {"type": "text", "text": prompt}]}])
