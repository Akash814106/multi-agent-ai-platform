from contextvars import ContextVar
from uuid import uuid4

from fastapi import HTTPException
from starlette.datastructures import MutableHeaders


request_id = ContextVar("request_id", default=None)
MAX_CHAT_BODY_BYTES = 96 * 1024


class RequestTrace:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        identifier = uuid4().hex
        context = request_id.set(identifier)
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
            bounded = scope["method"] == "POST" and scope["path"].rstrip("/") in {"/chat", "/chat/stream"}
            await self.app(scope, limited_receive if bounded else receive, traced_send)
        finally:
            request_id.reset(context)
