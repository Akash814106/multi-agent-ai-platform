import os
from functools import cached_property

from langchain_groq import ChatGroq

from backend.utils import env_loader  # Load backend/.env before reading settings.
from backend.utils.provider_errors import StepError
from backend.utils.telemetry import active_metrics


DEFAULT_MODEL = "qwen/qwen3.8-27b"


class GroqClient:
    def __init__(self, key_slot):
        self.key_slot = key_slot
        self.model_name = os.getenv("GROQ_MODEL") or DEFAULT_MODEL

    @cached_property
    def model(self):
        key = os.getenv(f"GROQ_API_KEY{self.key_slot}") if self.key_slot else None
        key = key or os.getenv("GROQ_API_KEY")
        if not key or not key.strip() or key == "your_groq_api_key":
            raise StepError("provider_configuration")
        options = {"reasoning_effort": "none", "reasoning_format": "hidden"} if self.model_name == DEFAULT_MODEL else {}
        return ChatGroq(model=self.model_name, api_key=key, timeout=25, max_retries=0, **options)

    def invoke(self, *args, **kwargs):
        model = self.model
        metrics = active_metrics.get()
        if metrics:
            metrics.record_call()
        return model.invoke(*args, **kwargs)

    def stream(self, *args, **kwargs):
        model = self.model
        metrics = active_metrics.get()
        if metrics:
            metrics.record_call()
        yield from model.stream(*args, **kwargs)


def create_groq_client(key_slot=None):
    return GroqClient(key_slot)
