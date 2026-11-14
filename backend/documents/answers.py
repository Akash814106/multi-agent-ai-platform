import re

from backend.documents.store import retrieve
from backend.utils.chat_events import run_step
from backend.utils.provider_errors import StepError


INSUFFICIENT = "The selected documents do not provide enough information to answer this question."
CITATION = re.compile(r"\[(\d+)\]")
CITATION_GROUP = re.compile(
    r"\[((?:\s*(?:sources?\s+)?\d+\s*)(?:[,;]\s*(?:sources?\s+)?\d+\s*)*)\]",
    re.IGNORECASE,
)


def normalize_citations(text, valid_ids):
    def verified_citations(match):
        ids = dict.fromkeys(re.findall(r"\d+", match[1]))
        return " ".join(f"[{source_id}]" for source_id in ids if source_id in valid_ids)

    return CITATION_GROUP.sub(verified_citations, text)


def answer_documents(query, owner, document_ids, *, lookup=None, generate=None):
    sources = run_step(
        "Document Retrieval", "Finds relevant passages in the selected documents.",
        lookup or retrieve, owner, list(dict.fromkeys(document_ids)), query,
    )
    answer = INSUFFICIENT
    cited = []
    if sources:
        if generate is None:
            from backend.agents.worker_agents.direct_response_agent import direct_response_agent
            generate = direct_response_agent
        valid_ids = {str(source["id"]) for source in sources}

        pending = ""
        has_citation = False

        def visible_text(text):
            nonlocal pending, has_citation
            pending += text
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
            normalized = normalize_citations(reply, valid_ids)
            used_ids = set(CITATION.findall(normalized))
            if not used_ids:
                raise StepError("document_citation_missing")
            return normalized, [source for source in sources if str(source["id"]) in used_ids]

        answer, cited = run_step(
            "Direct Response Agent", "Answers using the retrieved document passages.",
            generate_cited_answer,
        )
    return {"final_summary": answer, "completion_status": "complete", "sources": cited}
