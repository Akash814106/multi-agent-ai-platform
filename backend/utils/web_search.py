import os

import requests
from exa_py import Exa

from backend.utils import env_loader
from backend.utils.chat_events import provider_call
from backend.utils.provider_errors import StepError


exa = Exa(api_key=os.getenv("EXA_API_KEY"))


def search_web(query):
    def search():
        # The installed SDK drops HTTP status and Retry-After on errors.
        with requests.post(
            f"{exa.base_url}/search",
            headers=exa.headers,
            json={"query": query, "numResults": 3, "contents": {"text": {"maxCharacters": 800}}},
            timeout=(4, 12),
        ) as response:
            response.raise_for_status()
            return response.json()

    data = provider_call("exa", search)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise StepError("search_failure")
    excerpts = []
    for result in data["results"]:
        if not isinstance(result, dict):
            continue
        title, url, text = (result.get(key) for key in ("title", "url", "text"))
        if isinstance(url, str) and url.startswith(("https://", "http://")) and isinstance(text, str) and text.strip():
            excerpts.append(f"Title: {title or 'Untitled'}\nURL: {url}\nContent: {text[:800]}")
    if not excerpts:
        raise StepError("no_evidence")
    return "\n\n".join(excerpts)
