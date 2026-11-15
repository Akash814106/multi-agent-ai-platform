import asyncio
import json
import os
import unittest
from contextlib import ExitStack, contextmanager
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import groq
import httpx
import requests
from pymongo.errors import OperationFailure

with patch.dict("os.environ", {
    "MONGO_URI": "mongodb://localhost:27017",
    "JWT_SECRET_KEY": "day-one-test-secret",
    "JWT_ALGORITHM": "HS256",
    "GROQ_API_KEY": "test-key",
    "EXA_API_KEY": "test-key",
}):
    from backend.api import main
    from backend.api.chat_stream import stream_chat
    from backend.auth.database import client as mongo_client
    from backend.auth.jwt_handler import create_access_token
    from backend.graph import nodes
    from backend.utils.chat_events import provider_call, run_step
    from backend.utils.provider_errors import StepError, retry_delay
    from backend.utils.groq_client import create_groq_client
    from backend.utils.web_search import search_web
    from backend.workflows import task_processor


def tearDownModule():
    mongo_client.close()


def events_from(response):
    return [
        (lines[0].removeprefix("event: "), {key: value for key, value in json.loads(lines[1].removeprefix("data: ")).items() if key != "request_id"})
        for frame in response.text.strip().split("\n\n")
        if (lines := frame.splitlines()) and lines[0].startswith("event: ")
    ]


class ChatStreamTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("backend.utils.request_trace.schedule_metrics"))
        self.client = TestClient(main.app)
        self.user = "workspace-test@example.com"
        token = create_access_token({"sub": self.user})
        self.headers = {"Authorization": f"Bearer {token}"}

    def test_routes_and_account_round_trip(self):
        users = {}
        collection = Mock()
        collection.find_one.side_effect = lambda filters: users.get(filters["email"])
        collection.insert_one.side_effect = lambda user: users.update({
            user["email"]: {**user, "_id": "test-user"},
        })
        password = "test-password"
        with patch.object(main, "users_collection", collection):
            self.assertEqual(self.client.get("/").status_code, 200)
            self.assertEqual(self.client.post("/register", json={
                "username": "Workspace Tester", "email": self.user, "password": password,
            }).status_code, 200)
            self.assertNotEqual(users[self.user]["password"], password)
            self.assertEqual(self.client.post("/login", json={
                "email": self.user, "password": "wrong",
            }).status_code, 401)
            login = self.client.post("/login", json={"email": self.user, "password": password})
            self.assertEqual(login.status_code, 200)

        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        with patch.object(main, "run_workflow", return_value={"final_summary": "Legacy response"}) as run:
            legacy = self.client.post("/chat", headers=headers, json={"query": "Hello"})
        run.assert_called_once_with("Hello", self.user)
        self.assertEqual(legacy.json(), {"user": self.user, "response": {"final_summary": "Legacy response"}})
        for route in ("/chat", "/chat/stream"):
            self.assertEqual(self.client.post(route, json={"query": "Hello"}).status_code, 401)
            self.assertEqual(self.client.post(route, headers={
                "Authorization": "Bearer invalid",
            }, json={"query": "Hello"}).status_code, 401)
        self.assertEqual(self.client.post("/chat/stream", headers=headers, json={"query": "  "}).status_code, 422)

    def test_account_database_failure_preserves_cors_and_hides_details(self):
        collection = Mock()
        collection.find_one.side_effect = OperationFailure("private database error", code=8000)
        origin = "http://localhost:5173"
        payload = {"username": "Workspace Tester", "email": self.user, "password": "test-password"}
        with patch.object(main, "users_collection", collection), self.assertLogs(main.logger, level="WARNING"):
            for path in ("/register", "/login"):
                with self.subTest(path=path):
                    response = self.client.post(path, json=payload, headers={"Origin": origin})
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.headers["access-control-allow-origin"], origin)
                    self.assertEqual(response.json(), {
                        "detail": "Account storage is temporarily unavailable. Please try again later.",
                    })

    def test_chat_input_limits_and_validation_do_not_echo_values(self):
        private = "private-field-value"
        for body in [
            {"query": " "}, {"query": "q" * 12001},
            {"query": "Hi", "document_ids": [private]},
            {"query": "Hi", "document_ids": ["a" * 64] * 6},
            {"query": "q" * 2001, "document_ids": ["a" * 64]},
        ]:
            # Include a database user ID so the document-length check is exercised.
            token = create_access_token({"sub": self.user, "user_id": "test-user"})
            response = self.client.post("/chat/stream", json=body, headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(private, response.text)
        oversized = self.client.post("/chat", headers={**self.headers, "Content-Type": "application/json"},
                                     content='{"query":"' + "q" * 100000 + '"}')
        self.assertEqual(oversized.status_code, 413)
        malformed = self.client.post("/chat", headers={**self.headers, "Content-Type": "application/json"},
                                     content='{"query":"' + private)
        self.assertEqual(malformed.status_code, 422)
        self.assertNotIn(private, malformed.text)
        bad_register = self.client.post("/register", json={"password": {"data": private}})
        self.assertEqual(bad_register.status_code, 422)
        self.assertNotIn(private, bad_register.text)
        self.assertNotEqual(oversized.headers["X-Request-ID"], malformed.headers["X-Request-ID"])

    def test_direct_stream_sends_only_visible_chunks_and_safe_activity(self):
        chunks = [SimpleNamespace(text="Hello "), SimpleNamespace(text="🌊"), SimpleNamespace(text="")]
        with (
            patch.object(nodes, "query_router_agent", return_value={"decision": "DIRECT_RESPONSE"}),
            patch.object(nodes, "retrieve_memory", return_value=[["private saved context"]]),
            patch.object(nodes, "memory_save_agent", return_value="SKIP_MEMORY"),
            patch("backend.agents.worker_agents.direct_response_agent.llm") as model,
        ):
            model.stream.return_value = (chunk for chunk in chunks)
            response = self.client.post("/chat/stream", headers=self.headers, json={"query": "Hi"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertNotIn("private saved context", response.text)
        events = events_from(response)
        self.assertEqual([data["text"] for name, data in events if name == "delta"], ["Hello ", "🌊"])
        self.assertEqual(events[-1], ("done", {"answer": "Hello 🌊"}))
        running = [data for name, data in events if name == "activity" and data["status"] == "running"]
        self.assertEqual([data["name"] for data in running], [
            "Query Router Agent", "Memory Retrieval", "Direct Response Agent", "Memory Save Agent",
        ])
        completed = [data for name, data in events if name == "activity" and data["status"] == "completed"]
        self.assertEqual([data["id"] for data in running], [data["id"] for data in completed])
        self.assertTrue(all(data["duration_ms"] >= 0 for data in completed))

    def test_parallel_workflow_reports_revision_and_only_streams_final_summary(self):
        with (
            patch.object(nodes, "query_router_agent", return_value={"decision": "MULTI_AGENT_WORKFLOW"}),
            patch.object(nodes, "query_enhancement_agent", return_value="Enhanced request"),
            patch.object(nodes, "retrieve_memory", return_value=[[]]),
            patch.object(nodes, "planner_agent", return_value="Goal: A plan"),
            patch.object(nodes, "extract_tasks", return_value=["Task A", "Task B"]),
            patch.object(nodes, "memory_save_agent", return_value="SKIP_MEMORY"),
            patch.object(task_processor, "research_agent", return_value=("Research findings", "Source evidence", None)),
            patch.object(task_processor, "critic_agent", side_effect=lambda goal, task, result: {
                "score": 9 if result == "Revised text" else 5,
            }),
            patch.object(task_processor, "revision_agent", return_value="Revised text"),
            patch("backend.agents.worker_agents.summary_agent.llm") as model,
        ):
            model.stream.return_value = (chunk for chunk in [SimpleNamespace(text="Final summary")])
            response = self.client.post("/chat/stream", headers=self.headers, json={"query": "Make a plan"})
        model.invoke.assert_not_called()
        self.assertEqual(model.stream.call_count, 1)
        prompt = model.stream.call_args.args[0]
        self.assertIn("Original request:\n    Make a plan", prompt)
        self.assertIn("Source evidence", prompt)
        self.assertIn("Revised text", prompt)
        self.assertNotIn("User learned:", prompt)
        self.assertNotIn("Source evidence", response.text)
        events = events_from(response)
        self.assertEqual(events[-1], ("done", {"answer": "Final summary"}))
        self.assertEqual([data["text"] for name, data in events if name == "delta"], ["Final summary"])
        research = {}
        for name, data in events:
            if name == "activity" and data["name"] == "Research Agent":
                research.setdefault(data["id"], []).append(data["status"])
        self.assertEqual(list(research.values()), [["waiting", "running", "completed"]] * 2)
        revisions = [data for name, data in events if name == "activity"
                     and data["name"] == "Revision Agent" and data["status"] == "completed"]
        self.assertEqual(len({data["id"] for data in revisions}), 2)

    def test_failed_agent_sends_a_safe_error(self):
        with (
            patch.object(nodes, "query_router_agent", side_effect=RuntimeError("private provider detail")),
            self.assertLogs("backend.utils.provider_errors", level="WARNING") as logs,
        ):
            response = self.client.post("/chat/stream", headers=self.headers, json={"query": "Hi"})
        events = events_from(response)
        self.assertEqual(events[-2][1]["status"], "failed")
        self.assertEqual(events[-2][1]["error_category"], "workflow")
        self.assertEqual(events[-1][0], "error")
        self.assertNotIn("private provider detail", response.text)
        self.assertNotIn("private provider detail", "\n".join(logs.output))

    @contextmanager
    def research_workflow(self, **agents):
        with ExitStack() as stack:
            for name, value in {
                "query_router_agent": {"decision": "MULTI_AGENT_WORKFLOW"},
                "query_enhancement_agent": "Enhanced request",
                "retrieve_memory": [[]],
                "planner_agent": "Goal: Compare sectors\n1. Healthcare\n2. Finance\n3. Education",
            }.items():
                stack.enter_context(patch.object(nodes, name, return_value=value))
            for name, action in agents.items():
                stack.enter_context(patch.object(task_processor, name, side_effect=action))
            yield stack

    def test_partial_answer_retains_findings_and_skips_memory(self):
        def research(topic, **_):
            if "Finance" in topic:
                raise StepError("rate_limit")
            return ("Usable findings", "Source excerpt", "search_failure" if "Education" in topic else None)

        def critic(*_):
            raise StepError("invalid_response")

        with self.research_workflow(research_agent=research, critic_agent=critic) as stack:
            model = stack.enter_context(patch("backend.agents.worker_agents.summary_agent.llm"))
            memory = stack.enter_context(patch.object(nodes, "memory_save_agent"))
            model.stream.return_value = (chunk for chunk in [SimpleNamespace(text="Partial comparison with limitations")])
            response = self.client.post("/chat/stream", headers=self.headers, json={"query": "Compare three sectors"})
        memory.assert_not_called()
        prompt = model.stream.call_args.args[0]
        self.assertIn("Healthcare", prompt)
        self.assertIn("Finance: Research could not be completed", prompt)
        self.assertIn("Education: No usable web evidence", prompt)
        events = events_from(response)
        self.assertEqual(events[-1], ("done", {
            "answer": "Partial comparison with limitations", "completion_status": "partial",
        }))
        reasons = {data["error_category"] for name, data in events if name == "activity" and "error_category" in data}
        self.assertEqual(reasons, {"invalid_response", "rate_limit"})

    def test_all_failed_research_returns_error_without_synthesis_or_memory(self):
        def research(*_, **__):
            raise StepError("timeout")

        with self.research_workflow(research_agent=research) as stack:
            synthesize = stack.enter_context(patch.object(nodes, "synthesize_answer"))
            memory = stack.enter_context(patch.object(nodes, "memory_save_agent"))
            response = self.client.post("/chat/stream", headers=self.headers, json={"query": "Compare sectors"})
        synthesize.assert_not_called()
        memory.assert_not_called()
        events = events_from(response)
        self.assertEqual(events[-1][0], "error")
        self.assertNotIn("delta", [name for name, _ in events])

    def test_direct_answer_survives_memory_storage_failure(self):
        with (
            patch.object(nodes, "query_router_agent", return_value={"decision": "DIRECT_RESPONSE"}),
            patch.object(nodes, "retrieve_memory", return_value=[[]]),
            patch.object(nodes, "memory_save_agent", return_value="SAVE_MEMORY"),
            patch.object(nodes, "save_memory", side_effect=RuntimeError("private memory error")) as save,
            patch("backend.agents.worker_agents.direct_response_agent.llm") as model,
        ):
            model.stream.return_value = (chunk for chunk in [SimpleNamespace(text="Direct answer")])
            response = self.client.post("/chat/stream", headers=self.headers, json={"query": "Hi"})
        save.assert_called_once_with("Direct answer", self.user)
        self.assertEqual(events_from(response)[-1], ("done", {"answer": "Direct answer"}))
        self.assertNotIn("private memory error", response.text)

    def test_stream_failure_after_visible_output_does_not_restart_generation(self):
        def chunks():
            yield SimpleNamespace(text="Partial output")
            raise requests.Timeout("private upstream detail")

        with (
            patch.object(nodes, "query_router_agent", return_value={"decision": "DIRECT_RESPONSE"}),
            patch.object(nodes, "retrieve_memory", return_value=[[]]),
            patch("backend.agents.worker_agents.direct_response_agent.llm") as model,
        ):
            model.stream.side_effect = lambda *_args, **_kwargs: chunks()
            response = self.client.post("/chat/stream", headers=self.headers, json={"query": "Hi"})
        model.stream.assert_called_once()
        events = events_from(response)
        self.assertEqual([data["text"] for name, data in events if name == "delta"], ["Partial output"])
        self.assertEqual(events[-2][1]["error_category"], "timeout")
        self.assertEqual(events[-1][0], "error")
        self.assertNotIn("private upstream detail", response.text)


class ProviderReliabilityTests(unittest.TestCase):
    @staticmethod
    def http_error(status, retry_after):
        response = requests.Response()
        response.status_code = status
        response.headers["Retry-After"] = str(retry_after)
        return requests.HTTPError("private upstream detail", response=response)

    def test_transient_retry_is_bounded_and_honors_retry_after(self):
        error = self.http_error(429, 1)
        action = Mock(side_effect=[error, "success"])
        with patch("backend.utils.chat_events.wait_to_retry") as wait:
            self.assertEqual(provider_call("exa", action), "success")
        self.assertEqual(action.call_count, 2)
        self.assertGreaterEqual(wait.call_args.args[0], 1)
        self.assertIsNone(retry_delay(self.http_error(429, 60)))
        self.assertIsNone(retry_delay(self.http_error(429, 10)))
        self.assertGreaterEqual(retry_delay(self.http_error(429, 10), maximum=15), 10)
        self.assertIsNone(retry_delay(self.http_error(401, 1)))
        action = Mock(side_effect=error)
        with patch("backend.utils.chat_events.wait_to_retry"), self.assertRaises(StepError):
            provider_call("exa", action)
        self.assertEqual(action.call_count, 2)

    def test_invalid_critic_score_is_classified_without_logging_output(self):
        with patch("backend.agents.worker_agents.critic_agent.llm") as model:
            model.invoke.return_value.content = '{"score": "private invalid value"}'
            with self.assertRaises(StepError) as caught:
                task_processor.critic_agent("Goal", "Task", "Research")
        self.assertEqual(caught.exception.category, "invalid_response")

    def test_groq_transient_and_configuration_failures(self):
        request = httpx.Request("POST", "https://example.invalid")
        response = httpx.Response(429, request=request, headers={"Retry-After": "1"})
        rate_limit = groq.RateLimitError("private", response=response, body=None)
        for failure in (rate_limit, groq.APITimeoutError(request)):
            action = Mock(side_effect=failure)
            with patch("backend.utils.chat_events.wait_to_retry"), self.assertRaises(StepError) as caught:
                provider_call("groq", action)
            self.assertEqual(action.call_count, 2)
            self.assertIn(caught.exception.category, {"timeout", "rate_limit"})

        invalid = groq.NotFoundError("private", response=httpx.Response(404, request=request), body=None)
        action = Mock(side_effect=invalid)
        with self.assertRaises(StepError) as caught:
            provider_call("groq", action)
        action.assert_called_once()
        self.assertEqual(caught.exception.category, "provider_configuration")
        without_keys = {key: value for key, value in os.environ.items() if not key.startswith("GROQ_API_KEY")}
        with patch.dict(os.environ, without_keys, clear=True), self.assertRaises(StepError) as missing:
            provider_call("groq", lambda: create_groq_client().invoke("question"))
        self.assertEqual(missing.exception.category, "provider_configuration")

    def test_search_requests_source_text_and_ignores_results_without_it(self):
        with patch("backend.utils.web_search.requests.post") as post:
            response = post.return_value.__enter__.return_value
            response.json.return_value = {"results": [
                {"title": "Evidence", "url": "https://example.com/source", "text": "Source excerpt."},
                {"title": "Empty result", "url": "https://example.com/empty", "text": None},
            ]}
            result = search_web("Example query")
        self.assertIn("text", post.call_args.kwargs["json"]["contents"])
        self.assertIn("https://example.com/source", result)
        self.assertNotIn("https://example.com/empty", result)


class ChatCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_deadline_returns_one_terminal_error(self):
        finished = Event()

        def run(*_):
            try:
                run_step("Working agent", "Waits.", release.wait, 2)
            finally:
                finished.set()

        release = Event()
        with (
            patch("backend.api.chat_stream.run_workflow", side_effect=run),
            patch("backend.api.chat_stream.CHAT_DEADLINE_SECONDS", 0.15),
        ):
            received = [frame async for frame in stream_chat("Hello", "test")]
            release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait, 2))
        self.assertEqual(sum(frame.startswith("event: error") for frame in received), 1)
        self.assertIn("timed out", received[-1])
        self.assertFalse(any(frame.startswith("event: done") for frame in received))

    async def test_closing_the_stream_stops_following_agent_calls(self):
        release = Event()
        finished = Event()
        next_step = Mock()

        def run(query, user):
            try:
                run_step("First agent", "Works on the request.", release.wait, 2)
                run_step("Next agent", "Would continue the request.", next_step)
            finally:
                finished.set()

        with patch("backend.api.chat_stream.run_workflow", side_effect=run):
            stream = stream_chat("Hello", "workspace-test@example.com")
            self.assertEqual(await anext(stream), ": connected\n\n")
            self.assertIn('"status": "running"', await asyncio.wait_for(anext(stream), timeout=2))
            await stream.aclose()
            release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait, 2))
        next_step.assert_not_called()


if __name__ == "__main__":
    unittest.main()
