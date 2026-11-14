import hashlib
import os
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from math import isfinite
from threading import BoundedSemaphore
from time import monotonic

from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from chromadb.errors import ChromaError
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from backend.documents.extraction import DocumentError, chunks_from, setting
from backend.utils.chat_events import chat_events
from backend.utils.provider_errors import StepError, log_failure


metadata = None
client = None
index_slots = BoundedSemaphore(1)
retrieval_slots = BoundedSemaphore(2)
INDEX_DEADLINE = 150
STALE_AFTER = timedelta(minutes=10)


@lru_cache(maxsize=1)
def vectors():
    chroma_client = client
    if chroma_client is None:
        from backend.memory.chroma_memory import client as chroma_client
    return document_collection(chroma_client, "document_chunks_minilm_v1")


def document_records():
    if metadata is not None:
        return metadata
    from backend.auth.database import db
    return db["uploaded_documents"]


def document_collection(chroma_client, name):
    return chroma_client.get_or_create_collection(
        name,
        embedding_function=DefaultEmbeddingFunction(),
        configuration={"hnsw": {"space": "cosine"}},
    )


def public_document(row):
    status = row["status"]
    if status in {"processing", "deleting"} and row["updated_at"] < stale_before():
        status = "failed" if status == "processing" else "delete_failed"
    return {
        "id": row["_id"],
        "filename": row["filename"],
        "size": row["size"],
        "status": status,
        "chunks": row.get("chunks", 0),
        "error": row.get("error") or {
            "failed": "Indexing did not finish. Upload the file again.",
            "delete_failed": "Deletion did not finish. Try deleting the document again.",
        }.get(status),
    }


def stale_before():
    return datetime.now(UTC).replace(tzinfo=None) - STALE_AFTER


def owner_filter(owner, document_id):
    return {"owner": owner, "_id": document_id}


def chunk_filter(owner, document_ids):
    return {"$and": [{"owner": owner}, {"document_id": {"$in": list(document_ids)}}]}


def document_id_for(owner, data, filename):
    # Keep a content-addressed ID within each account and preserve file type.
    digest = hashlib.sha256()
    digest.update(owner.encode())
    digest.update(b"\0")
    digest.update(filename[-4:].encode())
    digest.update(b"\0")
    digest.update(data)
    return digest.hexdigest()


def list_documents(owner):
    return [public_document(row) for row in document_records().find({"owner": owner}).sort("updated_at", -1)]


def get_document(owner, document_id, *, records=None):
    records = document_records() if records is None else records
    row = records.find_one(owner_filter(owner, document_id))
    if not row:
        raise DocumentError("Document not found.", 404)
    return public_document(row)


def index_document(owner, data, filename, notify, *, collection=None, records=None):
    records = document_records() if records is None else records
    document_id = document_id_for(owner, data, filename)
    scope = owner_filter(owner, document_id)
    existing = records.find_one(scope)
    if existing and existing["status"] == "indexed":
        return public_document(existing)
    if not index_slots.acquire(blocking=False):
        raise DocumentError("Another document is being indexed. Try again shortly.", 503)

    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        records.update_one({"_id": document_id}, {"$setOnInsert": {
            "owner": owner, "filename": filename, "size": len(data),
            "status": "failed", "updated_at": now,
        }}, upsert=True)
        row = records.find_one_and_update({
            **scope,
            "$or": [{"status": "failed"}, {"status": "processing", "updated_at": {"$lt": stale_before()}}],
        }, {"$set": {
            "status": "processing", "filename": filename, "error": None, "updated_at": now,
        }}, return_document=ReturnDocument.AFTER)
        if row is None:
            existing = get_document(owner, document_id, records=records)
            if existing["status"] == "indexed":
                return existing
            raise DocumentError("This document is being processed or deleted.", 409)

        processing = {**scope, "status": "processing", "updated_at": row["updated_at"]}
        try:
            notify(public_document(row))
            deadline = monotonic() + INDEX_DEADLINE
            chunks = chunks_from(data, filename)
            collection = vectors() if collection is None else collection
            clear_chunks(collection, owner, document_id)
            for offset in range(0, len(chunks), 16):
                if monotonic() > deadline:
                    raise DocumentError("Indexing took too long. Try a smaller document.")
                batch = chunks[offset:offset + 16]
                ids = [f"{document_id}:{offset + index}" for index in range(len(batch))]
                collection.upsert(
                    ids=ids, documents=[text for _, text in batch],
                    metadatas=[
                        {
                            "owner": owner, "document_id": document_id, "filename": filename,
                            "chunk_id": chunk_id, "chunk_index": offset + index + 1,
                            **({"page": page} if page is not None else {}),
                        }
                        for index, ((page, _), chunk_id) in enumerate(zip(batch, ids))
                    ],
                )
            if monotonic() > deadline:
                raise DocumentError("Indexing took too long. Try a smaller document.")
            row = records.find_one_and_update(processing, {"$set": {
                "status": "indexed", "chunks": len(chunks), "updated_at": datetime.now(UTC),
            }}, return_document=ReturnDocument.AFTER)
            if row is None:
                raise DocumentError("Indexing could not be confirmed. Try again.", 503)
            return public_document(row)
        except Exception as error:
            # Failed documents stay unavailable to retrieval. A retry clears
            # their staged vectors before writing stable chunk IDs again.
            message = str(error) if isinstance(error, DocumentError) else "The document could not be indexed. Try again."
            log_failure("document_index", error, "document_storage")
            try:
                records.update_one(processing, {"$set": {
                    "status": "failed", "error": message, "updated_at": datetime.now(UTC),
                }})
            except PyMongoError as update_error:
                log_failure("document_index_state", update_error, "document_storage")
            raise DocumentError(message, error.status if isinstance(error, DocumentError) else 503) from None
    finally:
        index_slots.release()


def clear_chunks(collection, owner, document_id):
    collection.delete(where=chunk_filter(owner, [document_id]))


def delete_document(owner, document_id):
    records = document_records()
    scope = owner_filter(owner, document_id)
    # Hide metadata from retrieval before deleting vectors. A failed deletion
    # stays hidden and can be retried from the same endpoint.
    row = records.find_one_and_update({
        **scope, "$or": [
            {"status": {"$in": ["indexed", "failed", "delete_failed"]}},
            {"status": {"$in": ["processing", "deleting"]}, "updated_at": {"$lt": stale_before()}},
        ],
    }, {"$set": {"status": "deleting", "updated_at": datetime.now(UTC)}},
        return_document=ReturnDocument.AFTER)
    if not row:
        get_document(owner, document_id)
        raise DocumentError("This document is being processed or deleted.", 409)
    deleting = {**scope, "status": "deleting", "updated_at": row["updated_at"]}
    try:
        clear_chunks(vectors(), owner, document_id)
        if records.delete_one(deleting).deleted_count != 1:
            raise DocumentError("Deletion could not be confirmed.", 503)
    except Exception as error:
        log_failure("document_delete", error, "document_storage")
        try:
            records.update_one(deleting, {"$set": {
                "status": "delete_failed", "error": "Deletion did not finish. Try deleting the document again.",
            }})
        except PyMongoError as update_error:
            log_failure("document_delete_state", update_error, "document_storage")
        raise DocumentError("Deletion did not finish. Try deleting the document again.", 503) from None


def require_indexed(owner, document_ids, records):
    rows = records.find({"owner": owner, "_id": {"$in": document_ids}, "status": "indexed"}, {"_id": 1})
    if {row["_id"] for row in rows} != set(document_ids):
        raise DocumentError("A selected document is missing or has not finished indexing.", 404)


def retrieve(owner, document_ids, question, *, top_k=None, collection=None, records=None):
    if not retrieval_slots.acquire(blocking=False):
        raise StepError("retrieval_busy")
    try:
        return query_documents(owner, document_ids, question, top_k=top_k, collection=collection, records=records)
    finally:
        retrieval_slots.release()


def max_cosine_distance():
    return max(0, min(1, float(os.getenv("DOCUMENT_MAX_COSINE_DISTANCE", "0.70"))))


def query_documents(owner, document_ids, question, *, top_k, collection, records):
    events = chat_events.get()
    if events:
        events.check_cancelled()
    records = document_records() if records is None else records
    top_k = setting("DOCUMENT_TOP_K", 5, 1, 8) if top_k is None else top_k
    budget = setting("DOCUMENT_CONTEXT_CHARS", 4500, 300, 8000)
    threshold = max_cosine_distance()
    try:
        require_indexed(owner, document_ids, records)
        matches = (vectors() if collection is None else collection).query(
            query_texts=[question], n_results=top_k, where=chunk_filter(owner, document_ids),
            include=["documents", "metadatas", "distances"],
        )
        if events:
            events.check_cancelled()
        require_indexed(owner, document_ids, records)
    except (PyMongoError, ChromaError, OSError, ValueError, RuntimeError) as error:
        log_failure("document_retrieval", error, "document_storage")
        raise StepError("document_storage") from None
    sources = []
    for passage, entry, distance in zip(matches["documents"][0], matches["metadatas"][0], matches["distances"][0]):
        if not isfinite(distance) or distance > threshold:
            continue
        if entry["owner"] != owner or entry["document_id"] not in document_ids:
            continue
        excerpt = passage[:min(budget, 1200)].strip()
        if not excerpt:
            continue
        sources.append({
            "id": len(sources) + 1, "document_id": entry["document_id"], "filename": entry["filename"],
            "page": entry.get("page"), "chunk_index": entry["chunk_index"],
            "chunk_id": entry["chunk_id"], "excerpt": excerpt,
        })
        budget -= len(excerpt)
        if budget <= 0:
            break
    return sources
