import asyncio
import json
import logging
from threading import Event
from time import monotonic

from starlette.concurrency import run_in_threadpool

from backend.utils.chat_events import ChatCancelled, ChatEvents, chat_events
from backend.utils.provider_errors import classify_error, log_failure
from backend.workflows.multi_agent_workflow import run_workflow


logger = logging.getLogger(__name__)


async def stream_chat(query, user):
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    stopped = Event()

    def emit(name, data):
        if not stopped.is_set():
            loop.call_soon_threadsafe(queue.put_nowait, (name, data))

    def execute():
        started = monotonic()
        stats = {"agent_executions": 0, "failed_executions": 0, "slowest_step": None, "slowest_ms": 0}
        outcome = "failed"

        def report(name, data):
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
            result = run_workflow(query, user)
            answer = result.get("final_summary")
            outcome = result["completion_status"]
            if outcome == "failed" or not answer:
                emit("error", {"message": "No research tasks produced usable findings. Please retry."})
            else:
                payload = {"answer": answer}
                if outcome == "partial":
                    payload["completion_status"] = "partial"
                emit("done", payload)
        except ChatCancelled:
            outcome = "cancelled"
        except Exception as error:
            category = classify_error(error)
            log_failure("chat_workflow", error, category)
            message = (
                "The AI provider reached its rate limit. Please try again in a moment."
                if category == "rate_limit"
                else "The workflow could not finish. Please retry."
            )
            emit("error", {"message": message})
        finally:
            logger.info(
                "workflow outcome=%s elapsed_ms=%d stats=%s provider_counts=%s",
                outcome, round((monotonic() - started) * 1000), stats, events.counts,
            )
            chat_events.reset(context_token)

    worker = asyncio.create_task(run_in_threadpool(execute))
    try:
        yield ": connected\n\n"
        while True:
            try:
                name, data = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield f"event: {name}\ndata: {json.dumps(data)}\n\n"
            if name in {"done", "error"}:
                break
    finally:
        stopped.set()
        worker.cancel()
