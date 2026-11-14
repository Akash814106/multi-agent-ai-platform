import json
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from random import uniform
from time import sleep

import groq
import requests


logger = logging.getLogger(__name__)

REASONS = {
    "rate_limit": "External provider rate limit exceeded.",
    "timeout": "External provider request timed out.",
    "network": "Could not connect to an external provider.",
    "search_failure": "Web search was unavailable.",
    "no_evidence": "Web search returned no usable source text.",
    "invalid_response": "The model returned an invalid response.",
    "provider_request": "The external provider rejected the request.",
    "provider_unavailable": "The external provider was temporarily unavailable.",
    "workflow": "This workflow step could not finish.",
    "unknown": "This step failed unexpectedly.",
}


class StepError(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__(REASONS[category])


def status_code(error):
    return getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)


def classify_error(error, provider=None):
    status = status_code(error)
    if isinstance(error, StepError):
        return error.category
    if status == 429:
        return "rate_limit"
    if status == 408 or isinstance(error, (groq.APITimeoutError, requests.Timeout, TimeoutError)):
        return "timeout"
    if isinstance(error, (groq.APIConnectionError, requests.ConnectionError)):
        return "network"
    if provider == "exa":
        return "search_failure"
    if status in {500, 502, 503, 504}:
        return "provider_unavailable"
    if isinstance(error, groq.BadRequestError) and error.code in {"json_validate_failed", "tool_use_failed"}:
        return "invalid_response"
    if status and 400 <= status < 500:
        return "provider_request"
    if isinstance(error, (json.JSONDecodeError, groq.APIResponseValidationError)):
        return "invalid_response"
    if isinstance(error, (KeyError, ValueError, TypeError, RuntimeError)):
        return "workflow"
    return "unknown"


def log_failure(stage, error, category, *, provider=None, attempt=None):
    code = getattr(error, "code", None)
    known_code = code if isinstance(code, str) and code in {
        "json_validate_failed", "tool_use_failed", "rate_limit_exceeded", "invalid_api_key",
    } else None
    logger.warning(
        "stage=%s provider=%s category=%s exception=%s status=%s code=%s attempt=%s",
        stage, provider, category, type(error).__name__, status_code(error), known_code, attempt,
    )


def retry_delay(error, *, maximum=3):
    status = status_code(error)
    if status not in {408, 429, 500, 502, 503, 504} and not isinstance(
        error, (groq.APIConnectionError, requests.Timeout, requests.ConnectionError)
    ):
        return None

    headers = getattr(getattr(error, "response", None), "headers", {})
    if headers.get("x-should-retry", "").lower() == "false":
        return None
    try:
        if "retry-after-ms" in headers:
            delay = float(headers["retry-after-ms"]) / 1000
        elif "retry-after" in headers:
            value = headers["retry-after"]
            try:
                delay = float(value)
            except ValueError:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        else:
            delay = 1 if status == 429 else 0.5
    except (ValueError, TypeError, OverflowError):
        return None
    if not 0 <= delay <= maximum:
        return None
    return delay + uniform(0, 0.25)


def wait_to_retry(delay, stopped):
    if stopped is None:
        sleep(delay)
    else:
        stopped.wait(delay)
