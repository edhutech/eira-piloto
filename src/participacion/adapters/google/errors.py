from __future__ import annotations


def is_expected_google_error(error: BaseException) -> bool:
    """Identify normal Google auth/API failures without importing SDK eagerly."""
    try:
        from google.auth.exceptions import GoogleAuthError, TransportError
    except ImportError:
        return False
    google_errors: tuple[type[BaseException], ...] = (GoogleAuthError, TransportError)
    try:
        from googleapiclient.errors import HttpError
    except ImportError:
        HttpError = None  # type: ignore[assignment]
    if HttpError is not None:
        google_errors += (HttpError,)
    return isinstance(error, google_errors)


def format_google_error(error: BaseException) -> str:
    name = type(error).__name__
    if name == "RefreshError":
        return ("Google authentication failed; run "
                "participacion-google-auth to authorize a fresh token")
    return f"Google service error ({name}); check credentials, permissions, and API availability"
