import re

from backend.documents.response_format import (
    CITATION, buffer_possible_json, format_structured_reply, normalize_citations,
    parse_structured_reply, wants_json,
)
from backend.documents.store import retrieve
from backend.utils.chat_events import run_step
from backend.utils.provider_errors import StepError
from backend.utils.telemetry import active_metrics


INSUFFICIENT = "The selected documents do not provide enough information to answer this question."


def answer_documents(query, owner, document_ids, *, lookup=None, generate=None):
    sources = run_step(
        "Document Retrieval", "Finds relevant passages in the selected documents.",
        lookup or retrieve, owner, list(dict.fromkeys(document_ids)), query,
    )
    metrics = active_metrics.get()
    if metrics:
        metrics.passages = len(sources)
    answer = INSUFFICIENT
    cited = []
    if sources:
        if generate is None:
            from backend.agents.worker_agents.direct_response_agent import direct_response_agent
            generate = direct_response_agent
        valid_ids = {str(source["id"]) for source in sources}
        json_requested = wants_json(query)

        pending = ""
        has_citation = False
        prose_started = False

        def visible_text(text):
            nonlocal pending, has_citation, prose_started
            pending += text
            if not prose_started:
                if json_requested or buffer_possible_json(pending):
                    return ""
                prose_started = True
            # Keep an open bracket until later chunks complete the marker.
            end = len(pending)
            bracket = pending.rfind("[")
            if bracket > pending.rfind("]"):
                end = bracket
            clean = normalize_citations(pending[:end], valid_ids)
            has_citation = has_citation or bool(CITATION.search(clean))
            if not has_citation:
                return ""
            pending = pending[end:]
            return clean

        def generate_cited_answer():
            reply = generate(
                query,
                evidence=[{"id": source["id"], "text": source["excerpt"]} for source in sources],
                stream_text=visible_text,
            )
            if reply.strip().rstrip(".").casefold() == INSUFFICIENT.rstrip(".").casefold():
                return INSUFFICIENT, []
            structured = parse_structured_reply(reply, required=json_requested)
            if structured is None:
                normalized = normalize_citations(reply, valid_ids)
                used_ids = set(CITATION.findall(normalized))
            else:
                normalized, used_ids = format_structured_reply(structured, valid_ids, json_requested=json_requested)
            if not normalized or not used_ids:
                raise StepError("document_citation_missing")
            return normalized, [source for source in sources if str(source["id"]) in used_ids]

        answer, cited = run_step(
            "Direct Response Agent", "Answers using the retrieved document passages.",
            generate_cited_answer,
        )
    return {"final_summary": answer, "completion_status": "complete", "sources": cited}
