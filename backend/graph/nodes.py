import os
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from backend.graph.state import AgentState

from backend.memory.chroma_memory import (
    retrieve_memory,
    save_memory,
)

from backend.workflows.task_processor import process_task, queue_research

from backend.agents.control_agents.query_router_agent import (
    query_router_agent,
)

from backend.agents.control_agents.query_enhancement_agent import (
    query_enhancement_agent,
)

from backend.agents.control_agents.memory_router_agent import (
    memory_router_agent,
)

from backend.agents.control_agents.memory_save_agent import (
    memory_save_agent,
)

from backend.agents.worker_agents.planner_agent import (
    planner_agent,
)

from backend.agents.worker_agents.summary_agent import (
    synthesize_answer,
)

from backend.agents.worker_agents.direct_response_agent import (
    direct_response_agent,
)

from backend.utils.chat_events import ChatCancelled, run_step
from backend.utils.provider_errors import StepError
from backend.utils.task_parser import extract_tasks

def research_workers():
    try:
        return max(1, min(3, int(os.getenv("RESEARCH_MAX_WORKERS", "2"))))
    except ValueError:
        return 2


def optional_step(*args):
    try:
        return run_step(*args)
    except ChatCancelled:
        raise
    except Exception:
        return None


def query_router_node(state: AgentState):

    router_result = run_step(
        "Query Router Agent", "Chooses how to answer the request.",
        query_router_agent, state["user_query"]
    )

    return {
        "router_decision": router_result["decision"]
    }

def query_enhancement_node(state: AgentState):

    enhanced_user_query = run_step(
        "Query Enhancement Agent", "Clarifies the request for the workflow.",
        query_enhancement_agent, state["user_query"]
    )

    return {
        "enhanced_user_query": enhanced_user_query
    }

def memory_node(state: AgentState):

    memories = optional_step(
        "Memory Retrieval", "Looks up relevant saved context.", retrieve_memory,
        state["enhanced_user_query"],
        state["current_user"],
    )

    if memories and memories[0]:

        memory_context = "\n".join(
            memories[0]
        )

    else:

        memory_context = ""

    enhanced_query = state["enhanced_user_query"]

    memory_decision = "SKIP_MEMORY"

    if memory_context:

        memory_decision = optional_step(
            "Memory Router Agent", "Decides whether saved context is relevant.", memory_router_agent,

            state["enhanced_user_query"],

            memory_context,

        )

        if not isinstance(memory_decision, str):
            memory_decision = "SKIP_MEMORY"
        if memory_decision.strip() == "USE_MEMORY":

            enhanced_query = f"""
Memory:
{memory_context}

Question:
{state["enhanced_user_query"]}
"""

    return {

        "memory_context": memory_context,

        "memory_decision": memory_decision,

        "enhanced_query": enhanced_query,

    }

def planner_node(state: AgentState):

    plan = run_step(
        "Planner Agent", "Breaks the request into research tasks.", planner_agent,
        state.get(
            "enhanced_query",
            state["enhanced_user_query"]
        )
    )

    goal = ""

    lines = plan.splitlines()

    for i, line in enumerate(lines):

        if line.startswith("Goal:"):

            extracted = line.replace(
                "Goal:",
                ""
            ).strip()

            if extracted:

                goal = extracted

            elif i + 1 < len(lines):

                goal = lines[i + 1].strip()

            break

    tasks = extract_tasks(plan)
    if not tasks:
        raise StepError("invalid_response")
    if len(tasks) > 4:
        tasks = [*tasks[:3], "; ".join(tasks[3:])]

    return {

        "goal": goal,

        "plan": plan,

        "tasks": tasks,

    }

def parallel_task_node(state: AgentState):
    with ThreadPoolExecutor(max_workers=research_workers()) as executor:
        futures = [
            executor.submit(
                copy_context().run, process_task, state["goal"], task,
                research=queue_research(),
            )
            for task in state["tasks"]
        ]
        results = [future.result() for future in futures]

    metrics = {
        **state.get("metrics", {}),
        "task_count": len(results),
        "revision_executed": sum(item["revision_executed"] for item in results),
        "revision_skipped": sum(item["revision_skipped"] for item in results),
        "failed_tasks": sum(item["status"] == "FAILED" for item in results),
    }
    return {"results": results, "metrics": metrics}


def final_summary_node(state: AgentState):
    findings = []
    gaps = []
    for result in state["results"]:
        task = result["task"]
        if result["revision"]:
            findings.append(
                f"Task: {task}\nFindings: {result['revision']}\n"
                f"Source excerpts: {result['sources'] or 'None available.'}"
            )
        if result["gaps"]:
            gaps.append(f"{task}: {' '.join(result['gaps'])}")

    if not findings:
        return {"final_summary": "", "completion_status": "failed"}

    answer = run_step(
        "Summary Agent", "Writes the answer to the original question.",
        synthesize_answer, state["user_query"], "\n\n".join(findings), "\n".join(gaps),
    )
    return {
        "final_summary": answer,
        "completion_status": "partial" if gaps else "complete",
    }


def memory_save_node(state: AgentState):

    if not state.get("final_summary") or state.get("completion_status") == "partial":

        return {}

    if state["router_decision"] == "DIRECT_RESPONSE":

        query = state["user_query"]

    else:

        query = state.get(
            "enhanced_user_query",
            state["user_query"]
        )

    save_decision = optional_step(
        "Memory Save Agent", "Decides whether the answer contains useful context to remember.",
        memory_save_agent,

        query,

        state["final_summary"]

    )

    if isinstance(save_decision, str) and save_decision.strip() == "SAVE_MEMORY":

        optional_step(
            "Memory Storage", "Saves selected context for later questions.", save_memory,

            state["final_summary"],

            state["current_user"]

        )

    return {}

def direct_response_node(state: AgentState):

    memories = optional_step(
        "Memory Retrieval", "Looks up relevant saved context.", retrieve_memory,

        state["user_query"],

        state["current_user"],

    )

    memory_context = ""

    enhanced_query = state["user_query"]

    if memories and memories[0]:

        memory_context = "\n".join(

            memories[0]

        )

        enhanced_query = f"""
Memory:
{memory_context}

Question:
{state["user_query"]}
"""

    result = run_step(
        "Direct Response Agent", "Writes the answer directly.", direct_response_agent,
        enhanced_query
    )

    return {

        "memory_context": memory_context,

        "final_summary": result,
        "completion_status": "complete",

    }



