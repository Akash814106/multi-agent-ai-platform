import asyncio
import json
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from backend.api.models import DocumentId
from backend.auth.dependencies import get_document_owner
from backend.documents import store
from backend.documents.extraction import DocumentError, safe_filename, upload_limit, validate_type
from backend.utils.provider_errors import log_failure
from backend.utils.request_trace import request_id


router = APIRouter(prefix="/documents", tags=["documents"])


def document_http_error(error):
    return HTTPException(status_code=error.status, detail=str(error))


@router.get("")
def list_documents(owner: str = Depends(get_document_owner)):
    return store.list_documents(owner)


@router.get("/{document_id}")
def get_document(document_id: DocumentId, owner: str = Depends(get_document_owner)):
    try:
        return store.get_document(owner, document_id)
    except DocumentError as error:
        raise document_http_error(error) from None


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: DocumentId, owner: str = Depends(get_document_owner)):
    try:
        store.delete_document(owner, document_id)
    except DocumentError as error:
        raise document_http_error(error) from None
    return Response(status_code=204)


@router.post("")
async def upload_document(request: Request, owner: str = Depends(get_document_owner)):
    try:
        filename = safe_filename(unquote(request.headers.get("X-Document-Filename", "")))
        maximum = upload_limit()
        length = request.headers.get("content-length")
        if length and int(length) > maximum:
            raise DocumentError("The file exceeds the upload size limit.", 413)
        data = bytearray()
        async with asyncio.timeout(30):
            async for chunk in request.stream():
                if len(data) + len(chunk) > maximum:
                    raise DocumentError("The file exceeds the upload size limit.", 413)
                data.extend(chunk)
        # Pass lengths and content checks before reporting indexing activity.
        await run_in_threadpool(validate_type, data, filename)
    except DocumentError as error:
        raise document_http_error(error) from None
    except TimeoutError:
        raise HTTPException(status_code=408, detail="The upload took too long.") from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid upload request.") from None

    async def index_events():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        connected = True

        def emit(event, detail):
            if connected and not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, (event, detail))

        def index():
            try:
                document = store.index_document(owner, data, filename, lambda row: emit("processing", row))
                emit("done", document)
            except DocumentError as error:
                emit("error", {"message": str(error)})
            except Exception as error:
                log_failure("document_index", error, "document_storage")
                emit("error", {"message": "Document storage is unavailable. Try again."})

        # An accepted upload completes indexing even if the browser disconnects.
        worker = asyncio.create_task(run_in_threadpool(index))
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event, detail = await asyncio.wait_for(queue.get(), 10)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event in {"done", "error"}:
                    detail = {**detail, "request_id": request_id.get()}
                yield f"event: {event}\ndata: {json.dumps(detail)}\n\n"
                if event in {"done", "error"}:
                    break
        finally:
            connected = False
            # The worker owns indexing through completion, even after disconnect.
            if not worker.done():
                worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    return StreamingResponse(
        index_events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
