from backend.utils.api_counter import increment
from backend.utils.chat_events import invoke_model
from backend.utils.groq_client import create_groq_client


llm = create_groq_client(4)

def revision_agent(goal, task, research_output, critic_feedback):

    prompt = f"""
    You are a Revision Agent.

    Your job is to improve the research content using the critic feedback.

    Goal:
    {goal}

    Task:
    {task}

    Original Research:
    {research_output}

    Critic Feedback:
    {critic_feedback}

    Rules:
    - Preserve all correct information.
    - Fix weaknesses identified by the critic.
    - Add missing information mentioned by the critic.
    - Improve clarity and completeness.
    - Replace vague explanations with specific explanations.
    - Expand important concepts when necessary.
    - Remove redundant information.
    - Ensure the revised content fully satisfies the task.
    - Ensure the revised content contributes toward the overall goal.
    - For system design topics, include architecture decisions, tradeoffs, scalability, reliability, and security considerations when relevant.
    - Produce a complete revised version, not a list of changes.
    - Do not blindly apply all critic suggestions.
    - Only incorporate suggestions that are relevant to the task and overall goal.
    - Ignore suggestions that do not improve the quality or accuracy of the content.

    Return only the revised content.
    """

    increment("revision")
    response = invoke_model(llm, prompt, max_tokens=650)

    return response.content
