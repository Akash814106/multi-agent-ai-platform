import os

from langchain_groq import ChatGroq

from backend.utils import env_loader  # Load backend/.env before reading settings.


DEFAULT_MODEL = "qwen/qwen3.8-27b"


def create_groq_client(key_slot=None):
    model = os.getenv("GROQ_MODEL") or DEFAULT_MODEL
    api_key = os.getenv(f"GROQ_API_KEY{key_slot}") if key_slot else None
    options = {"reasoning_effort": "none", "reasoning_format": "hidden"} if model == DEFAULT_MODEL else {}
    return ChatGroq(
        model=model,
        api_key=api_key or os.getenv("GROQ_API_KEY"),
        timeout=25,
        max_retries=0,
        **options,
    )
