import json
import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import chromadb
import mongomock
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

with patch.dict(os.environ, {
    "MONGO_URI": "mongodb://localhost:27017",
    "JWT_SECRET_KEY": "document-test-secret",
    "JWT_ALGORITHM": "HS256",
    "GROQ_API_KEY": "test-key",
    "EXA_API_KEY": "test-key",
}):
    from backend.api.main import app
    from backend.auth.jwt_handler import create_access_token
    from backend.documents import store
    from backend.documents.answers import INSUFFICIENT


FACT = "The cancellation period for the Azure Finch project is exactly seventeen days."
QUESTION = "What is the cancellation period of the Azure Finch project?"


def pdf_bytes(*texts, encrypted=False):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=200)
        if not text:
            continue
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    with BytesIO() as output:
        writer.write(output)
        return output.getvalue()


def frames(response):
    return [
        (lines[0][7:], json.loads(lines[1][6:]))
        for frame in response.text.strip().split("\n\n")
        if (lines := frame.splitlines()) and lines[0].startswith("event: ")
    ]


class DocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chroma = chromadb.EphemeralClient()
        cls.vector_client = patch.object(store, "client", cls.chroma)
        cls.vector_client.start()
        cls.addClassCleanup(cls.vector_client.stop)
        store.vectors.cache_clear()
        cls.collection = store.vectors()
        cls.addClassCleanup(cls.chroma.delete_collection, cls.collection.name)
        cls.addClassCleanup(store.vectors.cache_clear)

    def setUp(self):
        self.collection.delete(where={"owner": {"$in": ["a", "b"]}})
        documents = mongomock.MongoClient().test.uploaded_documents
        mock = patch.object(store, "metadata", documents)
        mock.start()
        self.addCleanup(mock.stop)
        self.client = self.enterContext(TestClient(app))
        self.a = self.headers("a")
        self.b = self.headers("b")

    @staticmethod
    def headers(owner):
        token = create_access_token({"sub": f"{owner}@example.invalid", "user_id": owner})
        return {"Authorization": f"Bearer {token}"}

    def upload(self, name, contents, headers=None):
        return self.client.post("/documents", content=contents, headers={
            **(headers if headers is not None else self.a), "X-Document-Filename": name,
        })

    def answer(self, ids, headers=None, *, stream=True, question=QUESTION):
        return self.client.post("/chat/stream" if stream else "/chat", headers=headers or self.a,
                                json={"query": question, "document_ids": ids})

    def test_upload_validation_and_pdf_safety(self):
        self.assertEqual(self.upload("a.txt", b"hello", headers={}).status_code, 401)
        for name, data, status in [
            ("a.zip", b"hello", 415), ("a.pdf", b"hello", 415),
            ("a.txt", b"\0binary", 415), ("a.txt", b"", 422),
        ]:
            with self.subTest(name=name, data=data):
                self.assertEqual(self.upload(name, data).status_code, status)
        with patch.dict(os.environ, {"DOCUMENT_MAX_BYTES": "5"}):
            self.assertEqual(self.upload("large.txt", b"abcdef").status_code, 413)
        for data, match in [
            (b"%PDF-invalid", "corrupt"),
            (pdf_bytes("Encrypted", encrypted=True), "Password-protected"),
            (pdf_bytes(""), "OCR"),
        ]:
            response = self.upload("test.pdf", data)
            received = frames(response)
            self.assertEqual([event for event, _ in received], ["processing", "error"])
            self.assertIn(match, received[-1][1]["message"])

    def test_pdf_selection_citations_and_single_groq_generation(self):
        pdf = self.upload("ignored%2Fpolicy.pdf", pdf_bytes("Welcome to the project.", FACT))
        events = frames(pdf)
        self.assertEqual([event for event, _ in events], ["processing", "done"])
        document = events[-1][1]
        self.assertEqual(document["filename"], "policy.pdf")
        self.assertEqual(document["status"], "indexed")
        txt = self.upload("other.txt", b"The Azure Finch project cancellation period is three days.")
        self.assertEqual(frames(txt)[-1][1]["status"], "indexed")

        def replies(*_args, **_kwargs):
            for text in ("It is seventeen days [9]. [", "1", "]"):
                yield SimpleNamespace(text=text)

        with (
            patch("backend.agents.worker_agents.direct_response_agent.llm") as model,
            patch("backend.api.chat_stream.run_workflow") as workflow,
        ):
            model.stream.side_effect = replies
            result = self.answer([document["id"]])
        workflow.assert_not_called()
        model.stream.assert_called_once()
        prompt = model.stream.call_args.args[0]
        self.assertIn(QUESTION, prompt)
        self.assertIn(FACT, prompt)
        self.assertNotIn("three days", prompt)
        events = frames(result)
        self.assertEqual({data["name"] for event, data in events if event == "activity"},
                         {"Document Retrieval", "Direct Response Agent"})
        for event, data in events:
            if event == "activity" and data["status"] == "completed":
                self.assertGreaterEqual(data["duration_ms"], 0)
        done = events[-1][1]
        self.assertNotIn("[9]", result.text)
        self.assertEqual("".join(data["text"] for event, data in events if event == "delta"), done["answer"])
        source, = done["sources"]
        self.assertEqual((source["id"], source["filename"], source["page"]), (1, "policy.pdf", 2))
        self.assertEqual(source["excerpt"], FACT)

    def test_dedup_and_owner_isolation_through_delete(self):
        binary = FACT.encode()
        first = frames(self.upload("policy.txt", binary))[-1][1]
        repeated = frames(self.upload("renamed.txt", binary))
        self.assertEqual(repeated, [("done", first)])
        self.assertEqual(self.client.get("/documents", headers=self.a).json(), [first])
        self.assertEqual(self.client.get("/documents", headers=self.b).json(), [])
        url = "/documents/" + first["id"]
        self.assertEqual(self.client.get(url, headers=self.b).status_code, 404)
        self.assertEqual(self.client.delete(url, headers=self.b).status_code, 404)
        self.assertEqual(self.answer([first["id"]], self.b, stream=False).status_code, 404)
        with patch("backend.agents.worker_agents.direct_response_agent.llm") as model:
            denied = self.answer([first["id"]], self.b)
        model.stream.assert_not_called()
        self.assertEqual(frames(denied)[-1][0], "error")
        self.assertNotIn(FACT, denied.text)
        same_file_other_owner = frames(self.upload("policy.txt", binary, self.b))[-1][1]
        self.assertNotEqual(first["id"], same_file_other_owner["id"])
        self.assertEqual(self.client.delete(url, headers=self.a).status_code, 204)
        self.assertEqual(self.answer([first["id"]], stream=False).status_code, 404)
        self.assertEqual(self.collection.get(where=store.chunk_filter("a", [first["id"]]))["ids"], [])
        self.assertEqual(frames(self.upload("policy.txt", binary))[-1][1]["id"], first["id"])

    def test_insufficient_evidence_and_txt_citation(self):
        document = frames(self.upload("policy.txt", FACT.encode()))[-1][1]
        with patch("backend.agents.worker_agents.direct_response_agent.llm") as model:
            unrelated = self.answer([document["id"]], question="How many moons orbit Neptune?")
            model.stream.assert_not_called()
            self.assertEqual(frames(unrelated)[-1][1], {"answer": INSUFFICIENT, "sources": []})
            model.invoke.return_value.content = "Seventeen days [1]."
            response = self.answer([document["id"]], stream=False)
        source, = response.json()["response"]["sources"]
        self.assertEqual((source["filename"], source["page"], source["chunk_index"]), ("policy.txt", None, 1))
        self.assertEqual(source["excerpt"], FACT)
        with patch("backend.agents.worker_agents.direct_response_agent.llm") as model:
            model.stream.return_value = (SimpleNamespace(text=text) for text in ["Unsupported claim [", "9]"])
            rejected = self.answer([document["id"]])
        self.assertEqual(frames(rejected)[-1][1], {"answer": INSUFFICIENT, "sources": []})
        self.assertNotIn("Unsupported claim", rejected.text)

    def test_partial_delete_hides_document_and_can_retry(self):
        document = frames(self.upload("policy.txt", FACT.encode()))[-1][1]
        url = "/documents/" + document["id"]
        with patch.object(store, "clear_chunks", side_effect=RuntimeError("private error")):
            response = self.client.delete(url, headers=self.a)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private error", response.text)
        self.assertEqual(self.client.get(url, headers=self.a).json()["status"], "delete_failed")
        self.assertEqual(self.answer([document["id"]], stream=False).status_code, 404)
        self.assertEqual(self.client.delete(url, headers=self.a).status_code, 204)


if __name__ == "__main__":
    unittest.main()
