import asyncio
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import lru_cache
from threading import BoundedSemaphore, Lock
from time import monotonic

from starlette.concurrency import run_in_threadpool


logger = logging.getLogger(__name__)
active_metrics = ContextVar("active_metrics", default=None)
write_slots = BoundedSemaphore(16)
writes = set()
index_lock = Lock()


@lru_cache(maxsize=1)
def metrics_collection():
    from backend.auth.database import db
    collection = db["request_metrics"]
    with index_lock:
        collection.create_index([("owner", 1), ("started_at", -1), ("_id", -1)])
    return collection


class RequestMetrics:
    def __init__(self, identifier, started, timestamp):
        self.identifier = identifier
        self.started = started
        self.timestamp = timestamp
        self.owner = None
        self.workflow = None
        self.status = "cancelled"
        self.category = None
        self.steps = {}
        self.passages = None
        self.llm_calls = 0
        self.lock = Lock()

    def begin(self, owner, workflow):
        self.owner = owner if isinstance(owner, str) and owner else None
        self.workflow = workflow

    def finish(self, status, category=None):
        self.status = status
        self.category = category

    def activity(self, name, event):
        if name != "activity" or event["status"] == "waiting":
            return
        safe = {key: event[key] for key in ("id", "name", "status", "duration_ms", "error_category") if key in event}
        with self.lock:
            self.steps[safe["id"]] = safe

    def record_call(self):
        with self.lock:
            self.llm_calls += 1

    def snapshot(self):
        with self.lock:
            steps = []
            for step in sorted(self.steps.values(), key=lambda item: item["id"]):
                if step["status"] == "running":
                    steps.append({**step, "status": "incomplete"})
                else:
                    steps.append(step.copy())
            return {
                "_id": self.identifier, "owner": self.owner, "started_at": self.timestamp,
                "workflow": self.workflow, "status": self.status, "error_category": self.category,
                "duration_ms": round((monotonic() - self.started) * 1000),
                "steps": steps, "agent_count": len(steps), "retrieved_passages": self.passages,
                "llm_calls": self.llm_calls,
                "complete": self.status not in {"cancelled", "timeout"} and all(
                    step["status"] != "incomplete" for step in steps
                ),
            }


def start_metrics(identifier):
    return RequestMetrics(identifier, monotonic(), datetime.now(UTC))


def save_metrics(record):
    try:
        metrics_collection().update_one({"_id": record["_id"]}, {"$setOnInsert": record}, upsert=True)
    except Exception as error:
        logger.warning("request_id=%s telemetry write failed type=%s", record["_id"], type(error).__name__)


def schedule_metrics(metrics):
    if not metrics.owner:
        return
    record = metrics.snapshot()
    if not write_slots.acquire(blocking=False):
        logger.warning("request_id=%s telemetry write skipped: queue full", metrics.identifier)
        return

    async def write():
        await run_in_threadpool(save_metrics, record)

    def finished(task):
        writes.discard(task)
        write_slots.release()

    try:
        task = asyncio.create_task(write())
    except Exception:
        write_slots.release()
        raise
    writes.add(task)
    task.add_done_callback(finished)
