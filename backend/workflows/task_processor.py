from backend.agents.worker_agents.critic_agent import critic_agent
from backend.agents.worker_agents.research_agent import research_agent
from backend.agents.worker_agents.revision_agent import revision_agent
from backend.utils.chat_events import ChatCancelled, queue_step, run_step
from backend.utils.provider_errors import REASONS, classify_error


MAX_REVISIONS = 2
TARGET_SCORE = 8


def queue_research():
    return queue_step("Research Agent", "Researches a planned task.", research_agent)


def process_task(goal, task, *, research=None):
    research_result = None
    sources = ""
    best_result = None
    best_critic = None
    best_score = 0
    revision_executed = 0
    gaps = []
    error_category = None

    try:
        research_output = (research or research_agent)(
            f"Goal: {goal}\nTask: {task}", include_sources=True,
        )
        research_result, sources, search_error = research_output
        best_result = research_result
        if search_error:
            gaps.append("No usable web evidence was available for this task.")
        best_critic = run_step(
            "Critic Agent", "Reviews a task result for relevance and completeness.",
            critic_agent, goal, task, research_result,
        )
        best_score = best_critic["score"]
        for _ in range(MAX_REVISIONS):
            if best_score >= TARGET_SCORE:
                break
            revision_executed += 1
            revised = run_step(
                "Revision Agent", "Revises a result using the critique.",
                revision_agent, goal, task, best_result, best_critic,
            )
            critique = run_step(
                "Critic Agent", "Reviews the revised task result.",
                critic_agent, goal, task, revised,
            )
            if critique["score"] <= best_score:
                break
            best_result = revised
            best_critic = critique
            best_score = critique["score"]
    except ChatCancelled:
        raise
    except Exception as error:
        error_category = classify_error(error)
        gaps.append("Research could not be completed." if not best_result
                    else "Review or revision did not finish. Earlier findings were retained.")

    if best_critic is not None and best_score < TARGET_SCORE:
        gaps.append("The findings did not meet the review target.")
    status = "FAILED" if not best_result else "PARTIAL" if gaps else "SUCCESS"
    return {
        "task": task,
        "research": research_result,
        "critic": best_critic,
        "revision": best_result,
        "summary": best_result,
        "best_score": best_score,
        "revision_executed": revision_executed,
        "revision_skipped": int(revision_executed == 0 and best_critic is not None),
        "status": status,
        "sources": sources,
        "gaps": gaps,
        **({"error": REASONS[error_category], "error_category": error_category} if error_category else {}),
    }
