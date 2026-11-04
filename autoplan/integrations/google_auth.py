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
