"""Root logging setup.

The app had no logging configuration at all, so every logger call fell through
to logging's "last resort" handler: WARNING and above only, bare message, no
timestamp, no logger name. INFO lines vanished silently and warnings arrived
without any context. Configuring the root logger here fixes both.
"""

import logging

from app.config import settings
from app.observability import RequestIdFilter

# The request id makes lines from one request greppable together; it is "-" for
# anything logged outside a request (startup, background warm-up).
_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Tags our handler so repeat calls replace it instead of stacking duplicates,
# and so we never remove a handler somebody else (uvicorn, pytest) installed.
_HANDLER_NAME = "space-syntax-console"


def configure_logging() -> None:
    """Install the root console handler. Idempotent — safe to call repeatedly."""
    level = getattr(logging, settings.log_level.strip().upper(), logging.INFO)

    handler = logging.StreamHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    # On the handler, not a logger: records from third-party loggers reach it
    # too, and the format string needs request_id present on every record.
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn attaches its own handlers to the uvicorn.* loggers with
    # propagate=False, so they are unaffected either way — deliberately left
    # alone rather than re-pointed at ours.
