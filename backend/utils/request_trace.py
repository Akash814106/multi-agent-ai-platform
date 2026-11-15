from contextvars import ContextVar
import logging
from uuid import uuid4

from fastapi import HTTPException
from starlette.datastructures import MutableHeaders
from backend.utils.telemetry import active_metrics, schedule_metrics, start_metrics


request_id = ContextVar("request_id", default=None)
MAX_CHAT_BODY_BYTES = 96 * 1024
logger = logging.getLogger(__name__)


class RequestTrace:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        identifier = uuid4().hex
        context = request_id.set(identifier)
        bounded = scope["method"] == "POST" and scope["path"].rstrip("/") in {"/chat", "/chat/stream"}
        metrics = start_metrics(identifier) if bounded else None
        telemetry_context = active_metrics.set(metrics)
        remaining = MAX_CHAT_BODY_BYTES

        async def limited_receive():
            nonlocal remaining
            message = await receive()
            if message["type"] == "http.request":
                remaining -= len(message.get("body", b""))
                if remaining < 0:
                    raise HTTPException(413, "The chat request is too large.")
            return message

        async def traced_send(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = identifier
            await send(message)

        try:
            await self.app(scope, limited_receive if bounded else receive, traced_send)
        finally:
            if metrics:
                try:
                    schedule_metrics(metrics)
                except Exception as error:
                    logger.warning("request_id=%s telemetry scheduling failed type=%s", identifier, type(error).__name__)
            active_metrics.reset(telemetry_context)
            request_id.reset(context)
