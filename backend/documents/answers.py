import re

from backend.agents.worker_agents.direct_response_agent import direct_response_agent
from backend.documents.store import retrieve
from backend.utils.chat_events import run_step


INSUFFICIENT = "The selected documents do not provide enough information to answer this question."
CITATION = re.compile(r"\[(\d+)\]")


def answer_documents(query, owner, document_ids):
    sources = run_step(
        "Document Retrieval", "Finds relevant passages in the selected documents.",
        retrieve, owner, list(dict.fromkeys(document_ids)), query,
    )
    answer = INSUFFICIENT
    cited = []
    if sources:
        valid_ids = {str(source["id"]) for source in sources}

        def verified_citation(match):
            return match[0] if match[1] in valid_ids else ""

        pending = ""
        has_citation = False

        def visible_text(text):
            nonlocal pending, has_citation
            pending += text
            suffix = re.search(r"\[\d*$", pending)
            end = suffix.start() if suffix else len(pending)
            clean = CITATION.sub(verified_citation, pending[:end])
            has_citation = has_citation or bool(CITATION.search(clean))
            if not has_citation:
                return ""
            pending = pending[end:]
            return clean

        answer = run_step(
            "Direct Response Agent", "Answers using the retrieved document passages.",
            direct_response_agent, query,
            evidence=[{"id": source["id"], "text": source["excerpt"]} for source in sources],
            stream_text=visible_text,
        )
        answer = CITATION.sub(verified_citation, answer)
        used_ids = set(CITATION.findall(answer))
        cited = [source for source in sources if str(source["id"]) in used_ids]
        if not cited:
            answer = INSUFFICIENT
    return {"final_summary": answer, "completion_status": "complete", "sources": cited}
