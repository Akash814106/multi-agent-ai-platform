from backend.utils.api_counter import increment
from backend.utils.chat_events import invoke_model
from backend.utils.groq_client import create_groq_client


llm = create_groq_client(1)

def memory_router_agent(user_query,memory_context):

    prompt = f"""
    You are a Memory Router Agent.

    Determine whether the retrieved memory
    is relevant to answering the user's question.

    User Query:
    {user_query}

    Retrieved Memory:
    {memory_context}

    Return ONLY one of:

    USE_MEMORY

    or
    
    SKIP_MEMORY

    Do not explain your reasoning.
    Do not return any additional text.
    """

    increment("memory_router")
    response = invoke_model(llm, prompt)
    return response.content