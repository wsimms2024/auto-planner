"""
Google sign-in shared by Calendar and Gmail.

One-time setup: put the credentials.json you downloaded from Google Cloud
in the project folder. The first sign-in opens your browser; after that the
saved token in data/ is reused and refreshed automatically.
"""

from autoplan import config

SCOPES = [
    # Read your calendars, and create/update events on the AutoPlan calendar
    "https://www.googleapis.com/auth/calendar",
    # Read email (never sends or changes anything)
    "https://www.googleapis.com/auth/gmail.readonly",
]

CREDENTIALS_PATH = config.PROJECT_ROOT / "credentials.json"
TOKEN_PATH = config.PROJECT_ROOT / "data" / "google_token.json"


class NotSignedIn(Exception):
    """Raised when a sync runs before you've signed in with Google."""


def has_credentials_file() -> bool:
    return CREDENTIALS_PATH.exists()


def is_signed_in() -> bool:
    """A token is saved (it may still have expired; syncs report that)."""
    return TOKEN_PATH.exists()


def get_credentials(interactive: bool = False):
    """
    Return working Google credentials.

    interactive=True opens a browser to sign in if needed (only used when
    you click "Sign in with Google"). Background syncs pass False and raise
    NotSignedIn instead of popping a browser open.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save(creds)
            return creds
        except Exception:
            creds = None  # refresh token revoked or expired; sign in again

    if not interactive:
        raise NotSignedIn("Sign in with Google on the Sync page first.")
    if not has_credentials_file():
        raise FileNotFoundError(f"Put credentials.json in {CREDENTIALS_PATH.parent} first.")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save(creds)
    return creds


# ---------- Sign-in that works from any device ----------
#
# The old way (run_local_server) opened a browser on the computer running
# AutoPlan and froze the page until it finished, so clicking from a laptop or
# phone hung. Now the page shows a Google link. After you approve, Google sends
# your browser to http://localhost:8765/?code=... If you're on the AutoPlan
# computer, a tiny listener catches that automatically; on another device that
# page won't load, so you paste its address back into AutoPlan.

REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/"
_caught: dict[str, str] = {}  # state -> code, filled by the listener
_listener = None


def start_sign_in() -> tuple[object, str]:
    """(flow to keep until finish_sign_in, Google URL to open)."""
    import os

    from google_auth_oauthlib.flow import Flow

    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google may return scopes in a different order
    flow = Flow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES, redirect_uri=REDIRECT_URI)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    flow._autoplan_state = state
    _start_listener()
    return flow, url


def caught_code(flow) -> str | None:
    """The code the listener caught for this sign-in, if you approved on the AutoPlan computer."""
    return _caught.get(getattr(flow, "_autoplan_state", ""))


def token_works() -> bool:
    """The saved sign-in still works (refreshes it if needed). False if it expired or was revoked."""
    try:
        get_credentials(interactive=False)
        return True
    except NotSignedIn:
        return False


def code_from(pasted: str) -> str:
    """Accept the whole localhost address, or just the code."""
    from urllib.parse import parse_qs, urlparse

    pasted = pasted.strip()
    if "code=" in pasted:
        query = parse_qs(urlparse(pasted).query) if "://" in pasted else parse_qs(pasted.split("?", 1)[-1])
        if query.get("error"):
            raise ValueError(f"Google said: {query['error'][0]}")
        return query["code"][0]
    if "error=" in pasted:
        raise ValueError("Google didn't approve the sign-in. Try again.")
    return pasted


def finish_sign_in(flow, code: str):
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save(creds)
    return creds


def _start_listener() -> None:
    """Catch Google's redirect on this computer. Quietly skipped if the port is taken."""
    global _listener
    if _listener is not None:
        return
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if "code" in q and "state" in q:
                _caught[q["state"][0]] = q["code"][0]
                body = "Signed in. You can close this tab and go back to AutoPlan."
            else:
                body = "Sign-in didn't finish. Go back to AutoPlan and try again."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
    except OSError:
        return
    _listener = server
    threading.Thread(target=server.serve_forever, daemon=True).start()


def _save(creds) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")


def sign_out() -> None:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()


def calendar_service(interactive: bool = False):
    from googleapiclient.discovery import build

    return build("calendar", "v3", credentials=get_credentials(interactive), cache_discovery=False)


def gmail_service(interactive: bool = False):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=get_credentials(interactive), cache_discovery=False)
