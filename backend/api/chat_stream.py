import asyncio
import json
import logging
from threading import Event
from time import monotonic

from starlette.concurrency import run_in_threadpool

from backend.documents.answers import answer_documents
from backend.documents.extraction import DocumentError
from backend.utils.chat_events import ChatCancelled, ChatEvents, chat_events
from backend.utils.provider_errors import chat_failure_message, classify_error, log_failure
from backend.utils.request_trace import request_id
from backend.utils.telemetry import active_metrics
from backend.workflows.multi_agent_workflow import run_workflow


logger = logging.getLogger(__name__)
CHAT_DEADLINE_SECONDS = 280


async def stream_chat(query, user, *, owner=None, document_ids=()):
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    stopped = Event()
    deadline = monotonic() + CHAT_DEADLINE_SECONDS
    identifier = request_id.get()
    metrics = active_metrics.get()

    def emit(name, data, status=None, category=None):
        if not stopped.is_set():
            loop.call_soon_threadsafe(queue.put_nowait, (name, data, status, category))

    def execute():
        started = monotonic()
        stats = {"agent_executions": 0, "failed_executions": 0, "slowest_step": None, "slowest_ms": 0}
        outcome = "failed"

        def report(name, data):
            if metrics and not stopped.is_set():
                metrics.activity(name, data)
            if name == "activity" and "duration_ms" in data and not stopped.is_set():
                with events.lock:
                    stats["agent_executions"] += 1
                    stats["failed_executions"] += int(data["status"] == "failed")
                    if data["duration_ms"] > stats["slowest_ms"]:
                        stats["slowest_step"] = data["name"]
                        stats["slowest_ms"] = data["duration_ms"]
            emit(name, data)

        events = ChatEvents(report, stopped)
        context_token = chat_events.set(events)
        try:
            events.check_cancelled()
            result = answer_documents(query, owner, document_ids) if document_ids else run_workflow(query, user)
            answer = result.get("final_summary")
            outcome = result["completion_status"]
            if outcome == "failed" or not answer:
                emit("error", {"message": "No research tasks produced usable findings. Please retry."},
                     "failed", "workflow")
            else:
                payload = {"answer": answer}
                if document_ids:
                    payload["sources"] = result["sources"]
                if outcome == "partial":
                    payload["completion_status"] = "partial"
                emit("done", payload, "partial" if outcome == "partial" else "success")
        except ChatCancelled:
            outcome = "cancelled"
        except DocumentError as error:
            emit("error", {"message": str(error)}, "failed", "document_retrieval")
        except Exception as error:
            category = classify_error(error)
            log_failure("chat_workflow", error, category)
            emit("error", {"message": chat_failure_message(category)}, "failed", category)
        finally:
            logger.info(
                "request_id=%s workflow outcome=%s elapsed_ms=%d stats=%s provider_counts=%s",
                identifier, outcome, round((monotonic() - started) * 1000), stats, events.counts,
            )
            chat_events.reset(context_token)

    worker = asyncio.create_task(run_in_threadpool(execute))
    try:
        yield ": connected\n\n"
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                name, data = "error", {"message": chat_failure_message("timeout")}
                stopped.set()
                if metrics:
                    metrics.finish("timeout", "workflow_timeout")
                logger.warning("request_id=%s workflow deadline reached", identifier)
                yield sse_frame(name, data, identifier)
                break
            try:
                name, data, status, category = await asyncio.wait_for(queue.get(), timeout=min(15, remaining))
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if metrics and status:
                metrics.finish(status, category)
            yield sse_frame(name, data, identifier)
            if name in {"done", "error"}:
                break
    finally:
        stopped.set()
        worker.cancel()


def sse_frame(name, data, identifier):
    if name in {"done", "error"} and identifier:
        data = {**data, "request_id": identifier}
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"
