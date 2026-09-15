from __future__ import annotations

import time
from typing import Any


def execute_with_transient_retry(operation: Any, max_attempts: int = 3) -> Any:
    """Retry only Google-style 429 and 5xx responses."""
    for attempt in range(max_attempts):
        try:
            return operation()
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status != 429 and not (isinstance(status, int) and status >= 500):
                raise
            if attempt == max_attempts - 1:
                raise
            time.sleep(2 ** attempt)
