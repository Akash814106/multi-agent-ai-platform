import argparse
from datetime import UTC, datetime
import json
import re
from functools import partial
from pathlib import Path
from uuid import uuid4

import chromadb
import mongomock

from backend.documents import store
from backend.documents.extraction import DocumentError, setting


FIXTURE_CITATION = re.compile(r"\[([a-z]+:\d+)\]")
NUMBER_CITATION = re.compile(r"\[(\d+)\]")
DATASET = Path(__file__).with_name("dataset.json")


def retrieval_metrics(expected, retrieved):
    relevant = set(expected)
    seen = set(retrieved)
    hits = len(relevant & seen)
    return {
        "recall": hits / len(relevant) if relevant else None,
        "precision": hits / len(retrieved) if retrieved else 0.0,
        "hit": int(hits > 0) if relevant else None,
    }


def evaluate_citations(answer, cited, retrieved, truth, relevant, owner):
    returned = {str(item["id"]): item for item in retrieved}
    seen = {str(item["id"]) for item in cited}
    markers = set(NUMBER_CITATION.findall(answer))
    valid = seen == markers and len(seen) == len(cited)
    supported = bool(cited)
    for item in cited:
        reference = truth.get(item["chunk_id"], {})
        structural = returned.get(str(item["id"])) == item and reference.get("owner") == owner
        structural = structural and all(item.get(key) == reference.get(key) for key in (
            "document_id", "filename", "page", "chunk_index",
        ))
        structural = structural and bool(item["excerpt"]) and item["excerpt"] in reference.get("text", "")
        valid = valid and structural
        supported = supported and structural and reference.get("label") in relevant
    return {"structurally_valid": valid, "passage_support": supported if cited else None}


def facts_have_support(answer, sources, truth, fact_support):
    """Check the fixture's sentence-level citation links against human labels."""
    ids = {str(item["id"]): truth.get(item["chunk_id"], {}).get("label") for item in sources}
    sentences = re.split(r"(?<=[.!?])\s+|\n+", answer.casefold())
    return all(any(
        fact in sentence and any(ids.get(marker) in labels for marker in NUMBER_CITATION.findall(sentence))
        for sentence in sentences
    ) for fact, labels in fact_support.items())


def pdf_fixture(pages):
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=600, height=250)
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
        text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with BytesIO() as output:
        writer.write(output)
        return output.getvalue()


def index_fixtures(dataset, collection, records):
    documents = {}
    truth = {}
    for fixture in dataset["documents"]:
        filename = fixture["filename"]
        pages = fixture["pages"]
        binary = pdf_fixture(pages) if filename.endswith(".pdf") else "\n".join(pages).encode()
        row = store.index_document(fixture["owner"], binary, filename, lambda _: None,
                                   collection=collection, records=records)
        documents[fixture["key"]] = row["id"]
        # One deliberately short labelled passage per PDF page or TXT file.
        for index, page_text in enumerate(pages):
            chunk = f"{row['id']}:{index}"
            truth[chunk] = {
                "label": f"{fixture['key']}:{index + 1}", "owner": fixture["owner"],
                "document_id": row["id"], "filename": filename,
                "page": index + 1 if filename.endswith(".pdf") else None,
                "chunk_index": index + 1, "text": page_text,
            }
    return documents, truth


def evaluate_case(case, documents, truth, lookup):
    from backend.documents.answers import INSUFFICIENT, answer_documents

    selected = [documents[key] for key in case["selected"]]
    relevant = case["relevant"]
    retrieved = []

    def capture(*args):
        retrieved.extend(lookup(*args))
        return retrieved

    def fixture_answer(_query, *, evidence, stream_text):
        ids = {truth[item["chunk_id"]]["label"]: str(item["id"]) for item in retrieved}
        fixture = case.get("fixture")
        if not fixture or any(label not in ids for label in FIXTURE_CITATION.findall(fixture)):
            return INSUFFICIENT
        return FIXTURE_CITATION.sub(lambda match: "[" + ids[match[1]] + "]", fixture)

    try:
        result = answer_documents(case["question"], "alpha", selected, lookup=capture, generate=fixture_answer)
        denied = False
    except DocumentError as error:
        if error.status != 404:
            raise
        denied = True
        result = {"final_summary": "", "sources": []}

    labels = [truth.get(item["chunk_id"], {}).get("label", item["chunk_id"]) for item in retrieved]
    answer = result["final_summary"]
    citations = evaluate_citations(answer, result["sources"], retrieved, truth, relevant, "alpha")
    facts_match = all(fact in answer.casefold() for fact in case["facts"]) if case["answerable"] else None
    grounded = (
        citations["structurally_valid"] and facts_have_support(answer, result["sources"], truth, case["fact_support"])
        if case["answerable"] else None
    )
    abstained = answer == INSUFFICIENT if not case["answerable"] and not case.get("denied") else None
    safe = not any(value in answer.casefold() for value in case.get("forbidden", []))
    isolation = all(truth.get(item["chunk_id"], {}).get("owner") == "alpha" for item in retrieved)
    expected_denial = bool(case.get("denied"))
    metrics = retrieval_metrics(relevant, labels)
    passed = isolation and safe and denied == expected_denial and citations["structurally_valid"]
    if case["answerable"]:
        passed = passed and facts_match and grounded and citations["passage_support"] and metrics["recall"] == 1
    elif not expected_denial:
        passed = passed and abstained
    return {
        "id": case["id"], "answerable": case["answerable"], "passed": bool(passed),
        "expected_passages": relevant, "retrieved_passages": labels,
        **metrics, **citations, "facts_match": facts_match, "facts_have_labelled_support": grounded,
        "abstained": abstained,
        "denied": denied, "owner_isolated": isolation, "forbidden_output_absent": safe,
        "answer": answer,
    }


def average(rows, key):
    values = [row[key] for row in rows if row[key] is not None]
    return sum(values) / len(values) if values else None


def run_evaluation(top_k=5):
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    name = "rag_eval_" + uuid4().hex
    # No production database or credentials are involved. Only this uniquely
    # named in-memory collection is removed on exit.
    client = chromadb.EphemeralClient()
    collection = store.document_collection(client, name)
    records = mongomock.MongoClient().evaluation.documents
    try:
        documents, truth = index_fixtures(dataset, collection, records)
        lookup = partial(store.retrieve, top_k=top_k, collection=collection, records=records)
        cases = [evaluate_case(case, documents, truth, lookup) for case in dataset["cases"]]
    finally:
        client.delete_collection(name)
    answerable = [case for case in cases if case["answerable"]]
    return {
        "dataset_version": dataset["version"], "embedding_model": "all-MiniLM-L6-v2 (Chroma ONNX)",
        "configuration": {
            "distance": "cosine", "top_k": top_k,
            "max_cosine_distance": store.max_cosine_distance(),
            "context_chars": setting("DOCUMENT_CONTEXT_CHARS", 4500, 300, 8000),
            "chunk_chars": setting("DOCUMENT_CHUNK_CHARS", 900, 200, 1200),
            "chunk_overlap": setting("DOCUMENT_CHUNK_OVERLAP", 140, 0, setting("DOCUMENT_CHUNK_CHARS", 900, 200, 1200) // 3),
            "generation": "deterministic answer fixtures; zero Groq calls",
        },
        "metrics": {
            "recall_at_k": average(answerable, "recall"),
            "precision_at_k": average(answerable, "precision"),
            "hit_rate_at_k": average(answerable, "hit"),
            "citation_structure": average([case for case in cases if case["passage_support"] is not None], "structurally_valid"),
            "citation_passage_support": average(answerable, "passage_support"),
            "answer_fact_match": average(answerable, "facts_match"),
            "answer_labelled_support": average(answerable, "facts_have_labelled_support"),
            "insufficient_evidence_handling": average(cases, "abstained"),
            "passed_cases": sum(case["passed"] for case in cases),
            "total_cases": len(cases),
        },
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate document retrieval with real local embeddings and synthetic data.")
    parser.add_argument("--top-k", type=int, choices=range(1, 9), default=5)
    parser.add_argument("--output", type=Path, default=Path("rag_eval_results.json"))
    parser.add_argument("--publish-summary", action="store_true",
                        help="Refresh the dashboard's summary of this synthetic dataset.")
    args = parser.parse_args()
    report = run_evaluation(args.top_k)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.publish_summary:
        summary = {
            "dataset_version": report["dataset_version"], "embedding_model": report["embedding_model"],
            "top_k": args.top_k, "evaluated_at": datetime.now(UTC).isoformat(),
            **{key: report["metrics"][key] for key in (
                "total_cases", "passed_cases", "recall_at_k", "precision_at_k", "hit_rate_at_k",
            )},
        }
        path = Path(__file__).with_name("benchmark_summary.json")
        path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Dataset {report['dataset_version']} | {report['embedding_model']} | K={args.top_k}")
    for metric, value in report["metrics"].items():
        print(f"{metric}: {value if value is not None else 'n/a'}")
    for case in report["cases"]:
        if not case["passed"] or case["retrieved_passages"] != case["expected_passages"]:
            print(f"{case['id']}: expected {case['expected_passages']}, retrieved {case['retrieved_passages']}")
    print(f"JSON report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
