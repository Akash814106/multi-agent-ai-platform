import json
import re

from backend.utils.provider_errors import StepError


CITATION = re.compile(r"\[(\d+)\]")
CITATION_GROUP = re.compile(
    r"\[((?:\s*(?:sources?\s+)?\d+\s*)(?:[,;]\s*(?:sources?\s+)?\d+\s*)*)\]",
    re.IGNORECASE,
)
JSON_REQUEST = re.compile(
    r"\b(?:as|in)\s+(?:(?:valid|raw|strict|plain|a)\s+)*json\b"
    r"|\bjson\s+only\b"
    r"|\b(?:return|output|respond with|provide|give me|use)\s+(?:(?:only|valid|raw|a|an)\s+)*json\b",
    re.IGNORECASE,
)
JSON_FENCE = re.compile(r"^\s*\x60\x60\x60(?:json[ \t]*)?\r?\n(?P<body>.*?)\r?\n\x60\x60\x60\s*$", re.IGNORECASE | re.DOTALL)


def wants_json(question):
    for clause in re.split(r"[.!?;\n]+", question):
        if re.search(r"\b(?:do not|don[’']t|never|no)\b[^,]*\bjson\b", clause, re.IGNORECASE):
            continue
        if JSON_REQUEST.search(clause):
            return True
    return False


def normalize_citations(text, valid_ids):
    def verified_citations(match):
        ids = dict.fromkeys(re.findall(r"\d+", match[1]))
        return " ".join(f"[{source_id}]" for source_id in ids if source_id in valid_ids)

    return CITATION_GROUP.sub(verified_citations, text)


def buffer_possible_json(text):
    prefix = text.lstrip("\ufeff \t\r\n")
    if not prefix or prefix.startswith(("{", "[")):
        return True
    if not prefix.startswith("\x60"):
        return False
    if len(prefix) < 3:
        return all(character == "\x60" for character in prefix)
    if not prefix.startswith("\x60\x60\x60"):
        return False
    language, _, _ = prefix[3:].partition("\n")
    return language.strip().lower() in {"", "j", "js", "jso", "json"}


def parse_structured_reply(reply, *, required):
    fence = JSON_FENCE.fullmatch(reply)
    text = (fence["body"] if fence else reply).lstrip("\ufeff \t\r\n")
    if not required and not text.startswith(("{", "[")):
        return None
    try:
        result = json.loads(text, parse_constant=reject_constant)
    except ValueError:
        if not required and text.startswith("[") and re.match(r"^\[\d+\]\s+\S", text):
            return None
        raise StepError("invalid_response") from None
    if not isinstance(result, (dict, list)):
        raise StepError("invalid_response")
    return result


def reject_constant(_):
    raise ValueError("Expected standard JSON.")


def supplied_ids(value, valid_ids):
    values = value if isinstance(value, list) else [value]
    ids = []
    for entry in values:
        if type(entry) is int:
            label = str(entry)
        elif isinstance(entry, str):
            label = entry.strip().strip("[]").strip()
        else:
            continue
        if label in valid_ids and label not in ids:
            ids.append(label)
    return ids


def sanitize_json(value, valid_ids, used_ids):
    if isinstance(value, dict):
        clean = {}
        for key, entry in value.items():
            if key == "citations":
                ids = supplied_ids(entry, valid_ids)
                used_ids.update(ids)
                clean[key] = [int(source_id) for source_id in ids]
            else:
                clean[key] = sanitize_json(entry, valid_ids, used_ids)
        return clean
    if isinstance(value, list):
        return [sanitize_json(entry, valid_ids, used_ids) for entry in value]
    if isinstance(value, str):
        value = normalize_citations(value, valid_ids)
        used_ids.update(CITATION.findall(value))
    return value


def prose_from_json(value):
    if isinstance(value, list):
        return "\n\n".join(filter(None, (prose_from_json(item) for item in value)))
    if not isinstance(value, dict):
        return value.strip() if isinstance(value, str) else ""

    paragraphs = []
    for key, item in value.items():
        if key == "citations":
            continue
        if isinstance(item, (dict, list)):
            text = prose_from_json(item)
        elif isinstance(item, str):
            text = item.strip()
        elif type(item) in (int, float):
            text = str(item)
        else:
            continue
        if text:
            label = key.replace("_", " ").replace("-", " ").strip().capitalize()
            sentence = f"{label}: {text}" if label and len(text.split()) <= 5 and key != "answer" else text
            paragraphs.append(sentence if sentence[-1] in ".!?" else sentence + ".")

    result = " ".join(paragraphs)
    if not result:
        return ""
    markers = " ".join(f"[{source_id}]" for source_id in value.get("citations", [])
                       if f"[{source_id}]" not in result)
    if markers:
        if result[-1] in ".!?":
            return f"{result[:-1]} {markers}{result[-1]}"
        return f"{result} {markers}."
    return result


def format_structured_reply(value, valid_ids, *, json_requested):
    used_ids = set()
    sanitized = sanitize_json(value, valid_ids, used_ids)
    if json_requested:
        return "\x60\x60\x60json\n" + json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n\x60\x60\x60", used_ids
    answer = prose_from_json(sanitized)
    return answer, set(CITATION.findall(answer))
