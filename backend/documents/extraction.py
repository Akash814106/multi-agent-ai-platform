import json
import logging
import os
import re
import subprocess
import sys
import unicodedata
from io import BytesIO

from pypdf import PdfReader
from pypdf._configuration import DEFAULT_CONFIGURATION, apply_configuration


MAX_PAGES = 150
MAX_TEXT_CHARS = 240_000
MAX_CHUNKS = 360


class DocumentError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def setting(name, default, minimum, maximum):
    return max(minimum, min(maximum, int(os.getenv(name, str(default)))))


def upload_limit():
    return setting("DOCUMENT_MAX_BYTES", 10 * 1024 * 1024, 1, 20 * 1024 * 1024)


def safe_filename(name):
    basename = re.split(r"[/\\]", name)[-1]
    basename = "".join(c for c in basename if not unicodedata.category(c).startswith("C")).strip()
    stem, extension = os.path.splitext(basename)
    if extension.lower() not in {".pdf", ".txt"}:
        raise DocumentError("Choose a PDF or TXT file.", 415)
    return (stem.strip(". ")[:120] or "document") + extension.lower()


def validate_type(data, filename):
    if not data.strip():
        raise DocumentError("The file is empty.")
    if filename.endswith(".pdf"):
        if not data.startswith(b"%PDF-"):
            raise DocumentError("This file does not have a valid PDF header.", 415)
        return None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocumentError("TXT files must contain UTF-8 text.", 415) from None
    if data.startswith(b"%PDF-") or any(
        unicodedata.category(c) == "Cc" and c not in "\t\r\n" for c in text
    ):
        raise DocumentError("This file does not contain plain text.", 415)
    return text


def read_pdf(data):
    # Run in a separate process with a deadline so a malformed PDF cannot
    # keep the API worker occupied indefinitely.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.documents.extraction"],
            input=data, capture_output=True, timeout=20, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        payload = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise DocumentError("PDF extraction took too long. Try a shorter PDF.") from None
    except (OSError, subprocess.CalledProcessError, ValueError):
        raise DocumentError("The PDF could not be read.") from None
    if "error" in payload:
        raise DocumentError(payload["error"])
    return payload["pages"]


def extract_pdf(data):
    with apply_configuration(DEFAULT_CONFIGURATION.with_overwrites(
        maximum_declared_stream_length=12_000_000, zlib_maximum_output_length=12_000_000,
        stream_decoding_work_maximum_length=24_000_000, page_tree_maximum_entries=1500,
        page_tree_maximum_page_entries=30_000, xform_maximum_invocations_per_extraction=300,
        jbig2dec_binary=None,
    )):
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise DocumentError("Password-protected PDFs are unsupported.")
        if len(reader.pages) > MAX_PAGES:
            raise DocumentError(f"PDFs may contain at most {MAX_PAGES} pages.")
        pages = []
        total = 0
        for number, page in enumerate(reader.pages, 1):
            text = " ".join(page.extract_text().split())
            total += len(text)
            if total > MAX_TEXT_CHARS:
                raise DocumentError("The PDF contains too much text. Upload a smaller section.")
            if text:
                pages.append([number, text])
        return pages


def chunks_from(data, filename):
    text = validate_type(data, filename)
    pages = read_pdf(data) if text is None else [(None, " ".join(text.split()))]
    if sum(len(text) for _, text in pages) > MAX_TEXT_CHARS:
        raise DocumentError("The document contains too much text. Upload a smaller section.")

    size = setting("DOCUMENT_CHUNK_CHARS", 900, 200, 1200)
    overlap = setting("DOCUMENT_CHUNK_OVERLAP", 140, 0, size // 3)
    chunks = []
    for page, text in pages:
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                boundary = text.rfind(" ", start + size // 2, end)
                if boundary != -1:
                    end = boundary
            chunks.append((page, text[start:end].strip()))
            if len(chunks) > MAX_CHUNKS:
                raise DocumentError("The document needs too many chunks. Upload a smaller section.")
            if end == len(text):
                break
            start = end - overlap
    if not chunks:
        message = (
            "No usable text was found. Scanned PDFs require OCR and are unsupported."
            if filename.endswith(".pdf") else "The TXT file does not contain usable text."
        )
        raise DocumentError(message)
    return chunks


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        print(json.dumps({"pages": extract_pdf(sys.stdin.buffer.read())}))
    except DocumentError as error:
        print(json.dumps({"error": str(error)}))
    except Exception:
        print(json.dumps({"error": "The PDF is corrupt or uses an unsupported format."}))
