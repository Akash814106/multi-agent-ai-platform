from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from itertools import chain, count
from threading import Event, Lock
from time import monotonic
from typing import Callable
from backend.utils.provider_errors import (
    REASONS, StepError, classify_error, log_failure, retry_delay, wait_to_retry,
)


class ChatCancelled(Exception):
    pass


@dataclass
class ChatEvents:
    emit: Callable[[str, dict], None]
    stopped: Event

    def __post_init__(self):
        self.ids = count(1)
        self.lock = Lock()
        self.counts = {"groq_requests": 0, "exa_searches": 0, "retries": 0, "tokens": 0}

    def record(self, key, amount=1):
        with self.lock:
            self.counts[key] += amount

    def check_cancelled(self):
        if self.stopped.is_set():
            raise ChatCancelled


chat_events = ContextVar[ChatEvents | None]("chat_events", default=None)


def run_step(name, description, action, *args, **kwargs):
    events = chat_events.get()
    if events is None:
        try:
            return action(*args, **kwargs)
        except Exception as error:
            log_failure(name, error, classify_error(error))
            raise
    activity = {"id": next(events.ids), "name": name, "description": description}
    return execute_step(events, activity, action, *args, **kwargs)


def queue_step(name, description, action):
    events = chat_events.get()
    if events is None:
        return partial(run_step, name, description, action)
    events.check_cancelled()
    activity = {"id": next(events.ids), "name": name, "description": description}
    events.emit("activity", {**activity, "status": "waiting"})
    return partial(execute_step, events, activity, action)


def execute_step(events, activity, action, *args, **kwargs):
    events.check_cancelled()
    started = monotonic()
    events.emit("activity", {**activity, "status": "running"})
    status = "failed"
    details = {}
    try:
        result = action(*args, **kwargs)
        events.check_cancelled()
        status = "completed"
        return result
    except ChatCancelled:
        raise
    except Exception as error:
        category = classify_error(error)
        log_failure(activity["name"], error, category)
        details = {"error_category": category, "reason": REASONS[category]}
        raise
    finally:
        events.emit("activity", {
            **activity,
            "status": status,
            "duration_ms": round((monotonic() - started) * 1000),
            **details,
        })


def provider_call(provider, action, *, retry_window=3):
    """Retry a transient provider failure once, before any output is visible."""
    events = chat_events.get()
    for attempt in (1, 2):
        if events:
            events.check_cancelled()
            events.record("groq_requests" if provider == "groq" else "exa_searches")
            if attempt == 2:
                events.record("retries")
        try:
            return action()
        except Exception as error:
            category = classify_error(error, provider)
            log_failure("provider_call", error, category, provider=provider, attempt=attempt)
            delay = retry_delay(error, maximum=retry_window, attempt=attempt) if attempt == 1 else None
            if delay is None:
                raise StepError(category) from error
            wait_to_retry(delay, events.stopped if events else None)


def record_model_usage(response):
    events = chat_events.get()
    usage = getattr(response, "usage_metadata", None)
    if events and isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
        events.record("tokens", usage["total_tokens"])


def invoke_model(llm, prompt, **kwargs):
    response = provider_call("groq", lambda: llm.invoke(prompt, **kwargs))
    record_model_usage(response)
    return response


def final_answer(llm, prompt, *, stream_answer=True, retry_window=3, stream_text=None, **kwargs):
    events = chat_events.get()
    if events is None or not stream_answer:
        text = invoke_model(llm, prompt, **kwargs).content
        if not isinstance(text, str) or not text.strip():
            raise StepError("invalid_response")
        return text

    events.check_cancelled()
    parts = []

    def start_stream():
        chunks = llm.stream(prompt, **kwargs)
        try:
            return chunks, next(chunks)
        except Exception:
            chunks.close()
            raise

    chunks, first = provider_call("groq", start_stream, retry_window=retry_window)
    try:
        for chunk in chain((first,), chunks):
            events.check_cancelled()
            record_model_usage(chunk)
            if chunk.text:
                parts.append(chunk.text)
                visible = stream_text(chunk.text) if stream_text else chunk.text
                if visible:
                    events.emit("delta", {"text": visible})
    finally:
        chunks.close()
    text = "".join(parts)
    if not text.strip():
        raise StepError("invalid_response")
    return text
