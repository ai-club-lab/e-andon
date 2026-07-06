"""Structured JSON logging for Cloud Run (Req 10.4 observability).

Cloud Logging parses one-line JSON on stdout: ``severity`` sets the entry
level and ``message`` the text; remaining keys land in ``jsonPayload`` and are
filterable in Logs Explorer. Locally the plain human format is kept so dev
output stays readable (opt in with LOG_JSON=1).

Usage::

    from chokotei_shared import obs
    obs.setup_logging()
    logger.info("rca done", extra={"ctx": {"event_id": ev.event_id}})
"""
from __future__ import annotations

import json
import logging
import os
import sys

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "component": record.name,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            entry.update(ctx)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging() -> None:
    """Install the stdout handler once (JSON on Cloud Run, plain locally)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    use_json = bool(os.environ.get("K_SERVICE") or os.environ.get("LOG_JSON"))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _JsonFormatter() if use_json
        else logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    _CONFIGURED = True
