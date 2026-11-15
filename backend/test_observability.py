import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import mongomock
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect

from backend.test_chat_workspace import main, create_access_token
from backend.utils import telemetry
from backend.utils.chat_events import run_step
from backend.utils.groq_client import GroqClient
from backend.utils.provider_errors import StepError


class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_captures_cancelled_request(self):
        body = True
        running = asyncio.Event()
        release = Event()
        finished = Event()
        later_step = Event()
        captured = []

        async def receive():
            nonlocal body
            if body:
                body = False
                return {"type": "http.request", "body": b'{"query":"test"}', "more_body": False}
            await running.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if b'"status": "running"' in message.get("body", b""):
                running.set()

        def workflow(*_):
            try:
                run_step("Slow agent", "Waits", release.wait, 3)
                run_step("Later agent", "Later", later_step.set)
            finally:
                finished.set()

        token = create_access_token({"sub": "cancel@example.invalid", "user_id": "cancel"})
        scope = {
            "type": "http", "asgi": {"spec_version": "2.3"}, "http_version": "1.1",
            "method": "POST", "path": "/chat/stream", "query_string": b"",
            "headers": [(b"content-type", b"application/json"), (b"authorization", ("Bearer " + token).encode())],
        }
        with (
            patch("backend.api.chat_stream.run_workflow", side_effect=workflow),
            patch("backend.utils.request_trace.schedule_metrics", side_effect=lambda item: captured.append(item.snapshot())),
        ):
            await asyncio.wait_for(main.app(scope, receive, send), timeout=2)
            release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait, 2))
        record, = captured
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["steps"], [{"id": 1, "name": "Slow agent", "status": "incomplete"}])
        self.assertFalse(later_step.is_set())


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.collection = mongomock.MongoClient().test.request_metrics
        self.enterContext(patch("backend.api.observability.metrics_collection", return_value=self.collection))
        self.enterContext(patch.object(telemetry, "metrics_collection", return_value=self.collection))
        self.client = self.enterContext(TestClient(main.app))
        self.headers = self.auth("alpha")

    @staticmethod
    def auth(owner):
        token = create_access_token({"sub": owner + "@example.invalid", "user_id": owner})
        return {"Authorization": "Bearer " + token}

    def drain(self):
        async def wait():
            if telemetry.writes:
                await asyncio.gather(*tuple(telemetry.writes))
        self.client.portal.call(wait)

    def test_stream_and_legacy_persist_safe_independent_records(self):
        fake = SimpleNamespace(
            invoke=lambda *_: SimpleNamespace(content="answer"),
            stream=lambda *_: iter([SimpleNamespace(text="answer")]),
        )
        groq = GroqClient(1)
        groq.model = fake

        def workflow(query, user):
            run_step("Measured agent", "Private synthetic description", groq.invoke, query)
            return {"final_summary": "synthetic private answer", "completion_status": "complete"}

        with (
            patch("backend.api.chat_stream.run_workflow", side_effect=workflow),
            patch.object(main, "run_workflow", side_effect=workflow),
        ):
            responses = [self.client.post(path, json={"query": "synthetic private question"}, headers=self.headers)
                         for path in ("/chat/stream", "/chat", "/chat/stream")]
        self.drain()
        self.assertEqual(self.collection.count_documents({}), 3)
        ids = set()
        for response in responses:
            self.assertEqual(response.status_code, 200)
            identifier = response.headers["X-Request-ID"]
            ids.add(identifier)
            record = self.collection.find_one({"_id": identifier})
            self.assertEqual((record["owner"], record["status"], record["llm_calls"]), ("alpha", "success", 1))
            self.assertGreaterEqual(record["duration_ms"], record["steps"][0]["duration_ms"])
            self.assertNotIn("synthetic private", repr(record))
            self.assertNotIn("@example.invalid", repr(record))
            detail = self.client.get("/observability/requests/" + identifier, headers=self.headers)
            self.assertEqual(detail.json()["steps"][0]["name"], "Measured agent")
        self.assertEqual(len(ids), 3)
        self.assertIn(responses[0].headers["X-Request-ID"], responses[0].text)
        telemetry.save_metrics(self.collection.find_one({"_id": next(iter(ids))}))
        self.assertEqual(self.collection.count_documents({}), 3)

    def test_failed_stream_and_failed_write_are_independent_of_response(self):
        with patch("backend.api.chat_stream.run_workflow", side_effect=StepError("rate_limit")):
            failure = self.client.post("/chat/stream", json={"query": "test"}, headers=self.headers)
        self.drain()
        record = self.collection.find_one({"_id": failure.headers["X-Request-ID"]})
        self.assertEqual((record["status"], record["error_category"]), ("failed", "rate_limit"))
        self.assertIn("event: error", failure.text)
        with (
            patch.object(self.collection, "update_one", side_effect=AutoReconnect("private value")),
            patch("backend.api.chat_stream.run_workflow", return_value={
                "final_summary": "answer", "completion_status": "complete",
            }),
            self.assertLogs("backend.utils.telemetry") as captured,
        ):
            success = self.client.post("/chat/stream", json={"query": "test"}, headers=self.headers)
            self.drain()
        self.assertIn("event: done", success.text)
        self.assertIn(success.headers["X-Request-ID"], captured.output[0])
        self.assertNotIn("private value", captured.output[0])
        self.assertEqual(self.collection.count_documents({}), 1)

    def test_document_retrieval_count_and_partial_status(self):
        source = {"id": 1, "document_id": "a" * 64, "filename": "sample.txt", "chunk_id": "chunk-1",
                  "chunk_index": 1, "page": None, "excerpt": "private evidence"}
        with (
            patch("backend.documents.answers.retrieve", return_value=[source]),
            patch("backend.agents.worker_agents.direct_response_agent.llm") as model,
        ):
            model.stream.return_value = (SimpleNamespace(text="answer [1]") for _ in range(1))
            rag = self.client.post("/chat/stream", json={"query": "test", "document_ids": ["a" * 64]}, headers=self.headers)
        with patch("backend.api.chat_stream.run_workflow", return_value={
            "final_summary": "partial", "completion_status": "partial",
        }):
            partial = self.client.post("/chat/stream", json={"query": "test"}, headers=self.headers)
        self.drain()
        record = self.collection.find_one({"_id": rag.headers["X-Request-ID"]})
        self.assertEqual((record["workflow"], record["retrieved_passages"]), ("document_rag", 1))
        self.assertNotIn("private evidence", repr(record))
        self.assertEqual(self.collection.find_one({"_id": partial.headers["X-Request-ID"]})["status"], "partial")

    def test_cancelled_snapshot_does_not_invent_duration_or_failures(self):
        metrics = telemetry.start_metrics(uuid4().hex)
        metrics.begin("alpha", "general_chat")
        metrics.activity("activity", {"id": 1, "name": "Planner", "status": "waiting"})
        metrics.activity("activity", {"id": 1, "name": "Planner", "status": "running"})
        record = metrics.snapshot()
        self.assertEqual(record["status"], "cancelled")
        self.assertFalse(record["complete"])
        self.assertEqual(record["steps"], [{"id": 1, "name": "Planner", "status": "incomplete"}])

    def test_summary_pagination_and_owner_authorization(self):
        def insert(owner, days, status, duration, **fields):
            row = telemetry.start_metrics(uuid4().hex)
            row.timestamp = datetime.now(UTC) - timedelta(days=days)
            row.begin(owner, fields.get("workflow", "general_chat"))
            row.finish(status, fields.get("category"))
            record = row.snapshot()
            record.update(duration_ms=duration, **fields.get("extra", {}))
            self.collection.insert_one(record)
            return record

        recent = insert("alpha", 0.1, "success", 600, workflow="document_rag", extra={
            "retrieved_passages": 2, "agent_count": 1,
            "steps": [{"id": 1, "name": "Document Retrieval", "status": "completed", "duration_ms": 200}],
        })
        insert("alpha", 2, "failed", 400, category="rate_limit", extra={
            "steps": [{"id": 1, "name": "Direct Response Agent", "status": "failed", "duration_ms": 300,
                       "error_category": "rate_limit"}],
        })
        insert("alpha", 10, "partial", 1200)
        foreign = insert("beta", 0.1, "success", 100)
        dashboard = "/observability"
        for route in ("/summary", "/requests", "/benchmark", "/requests/" + recent["_id"]):
            self.assertEqual(self.client.get(dashboard + route).status_code, 401)
        summary = self.client.get(dashboard + "/summary", headers=self.headers).json()
        self.assertEqual((summary["total"], summary["success_rate"], summary["average_duration_ms"]), (2, 0.5, 500))
        self.assertEqual(summary["retrieval"], {"measured": 1, "average_passages": 2, "empty": 0})
        self.assertEqual(summary["failures"], {"rate_limit": 1})
        self.assertEqual(summary["step_failures"], {"rate_limit": 1})
        self.assertEqual(len(summary["agents"]), 2)
        for period, total in [("24h", 1), ("30d", 3)]:
            self.assertEqual(self.client.get(dashboard + "/summary?period=" + period, headers=self.headers).json()["total"], total)
        history = dashboard + "/requests?limit=1"
        page_one = self.client.get(history, headers=self.headers).json()
        page_two = self.client.get(history + "&page=2", headers=self.headers).json()
        self.assertTrue(page_one["has_more"])
        self.assertEqual(page_one["items"][0]["request_id"], recent["_id"])
        self.assertNotEqual(page_one["items"][0]["request_id"], page_two["items"][0]["request_id"])
        self.assertFalse(page_two["has_more"])
        self.assertEqual(self.client.get(dashboard + "/requests?limit=51", headers=self.headers).status_code, 422)
        self.assertEqual(self.client.get(dashboard + "/requests/" + foreign["_id"], headers=self.headers).status_code, 404)
        empty = self.client.get(dashboard + "/summary", headers=self.auth("empty")).json()
        self.assertEqual(empty["total"], 0)
        self.assertIsNone(empty["success_rate"])
        self.assertIsNone(empty["average_duration_ms"])
        general_only = self.client.get(dashboard + "/summary", headers=self.auth("beta")).json()
        self.assertIsNone(general_only["retrieval"])
        benchmark = self.client.get(dashboard + "/benchmark", headers=self.headers).json()["benchmark"]
        self.assertIn("recall_at_k", benchmark)
        self.assertNotIn("cases", benchmark)

    def test_stream_timeout_persists_incomplete_without_claiming_failure(self):
        release = Event()

        def workflow(*_):
            run_step("Slow agent", "Slow", release.wait, 2)

        with (
            patch("backend.api.chat_stream.run_workflow", side_effect=workflow),
            patch("backend.api.chat_stream.CHAT_DEADLINE_SECONDS", 0.2),
        ):
            response = self.client.post("/chat/stream", json={"query": "test"}, headers=self.headers)
            self.drain()
            release.set()
        record = self.collection.find_one({"_id": response.headers["X-Request-ID"]})
        self.assertEqual(record["status"], "timeout")
        self.assertFalse(record["complete"])
        self.assertEqual(record["steps"][0]["status"], "incomplete")
        self.assertNotIn("duration_ms", record["steps"][0])
