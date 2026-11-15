import logging
from threading import Event

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pymongo.errors import PyMongoError

from backend.api.chat_stream import stream_chat
from backend.api.documents import document_http_error, router as document_router
from backend.api.models import ChatRequest
from backend.api.observability import router as observability_router
from backend.auth.dependencies import document_owner, get_current_claims
from backend.documents.answers import answer_documents
from backend.documents.extraction import DocumentError
from backend.workflows.multi_agent_workflow import run_workflow

from backend.auth.models import (
    RegisterRequest,
    LoginRequest,
)

from backend.auth.database import users_collection
from backend.auth.auth import (
    hash_password,
    verify_password,
)
from backend.auth.jwt_handler import create_access_token
from backend.utils.provider_errors import chat_failure_message, classify_error, log_failure
from backend.utils.request_trace import RequestTrace, request_id
from backend.utils.chat_events import ChatEvents, chat_events
from backend.utils.telemetry import active_metrics

app = FastAPI()
app.include_router(document_router)
app.include_router(observability_router)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://agentflow-fhc0.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestTrace)


@app.exception_handler(RequestValidationError)
async def invalid_input(request, error):
    fields = {item["loc"][1] for item in error.errors() if (
        len(item["loc"]) > 1 and item["loc"][0] == "body" and isinstance(item["loc"][1], str)
    )}
    detail = "Check the request fields and try again."
    if request.url.path.rstrip("/") in {"/chat", "/chat/stream"}:
        if "document_ids" in fields:
            detail = "Select up to five valid document IDs."
        elif "query" in fields:
            detail = "Enter a nonblank message of at most 12,000 characters."
        else:
            detail = "Send a valid JSON chat request."
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(PyMongoError)
async def account_database_error(_request, error):
    logger.warning(
        "request_id=%s database request failed (%s, code=%s)",
        request_id.get(),
        type(error).__name__,
        getattr(error, "code", None),
    )
    return JSONResponse(
        status_code=503,
        content={"detail": (
            "Document storage is temporarily unavailable. Try again."
            if _request.url.path.startswith("/documents")
            else "Account storage is temporarily unavailable. Please try again later."
        )},
    )


@app.get("/")
def home():
    return {
        "message": "Multi Agent AI API is running"
    }


@app.post("/register")
def register(request: RegisterRequest):

    existing_user = users_collection.find_one(
        {
            "email": request.email
        }
    )

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Email already exists",
        )

    users_collection.insert_one(
        {
            "username": request.username,
            "email": request.email,
            "password": hash_password(request.password),
        }
    )

    return {
        "message": "Registration successful"
    }


@app.post("/login")
def login(request: LoginRequest):

    user = users_collection.find_one(
        {
            "email": request.email
        }
    )

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    if not verify_password(
        request.password,
        user["password"],
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    token = create_access_token(
        {
            "sub": user["email"],
            "user_id": str(user["_id"]),
        }
    )

    return {
        "access_token": token,
        "username": user["username"],
    }

@app.post("/chat")
def chat(
    request: ChatRequest,
    claims: dict = Depends(get_current_claims),
):
    current_user = claims["sub"]
    owner = document_owner(claims) if request.document_ids else None
    query = document_query(request) if request.document_ids else request.query
    metrics = active_metrics.get()
    if metrics:
        metrics.begin(claims.get("user_id"), "document_rag" if request.document_ids else "general_chat")
    context = chat_events.set(ChatEvents(metrics.activity, Event(), streaming=False)) if metrics else None
    try:
        result = (
            answer_documents(query, owner, request.document_ids)
            if request.document_ids else run_workflow(query, current_user)
        )
        if metrics:
            status = {"partial": "partial", "failed": "failed"}.get(result.get("completion_status"), "success")
            metrics.finish(status, "workflow" if status == "failed" else None)
    except DocumentError as error:
        if metrics:
            metrics.finish("failed", "document_retrieval")
        raise document_http_error(error) from None
    except Exception as error:
        category = classify_error(error)
        if metrics:
            metrics.finish("failed", category)
        log_failure("chat_workflow", error, category)
        raise HTTPException(status_code=502, detail=chat_failure_message(category)) from None
    finally:
        if context is not None:
            chat_events.reset(context)

    return {
        "user": current_user,
        "response": result
    }


def document_query(request):
    query = request.query.strip()
    if not 1 <= len(query) <= 2000:
        raise HTTPException(status_code=422, detail="Enter a document question of 1 to 2,000 characters.")
    return query


@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    claims: dict = Depends(get_current_claims),
):
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Enter a message.")
    owner = document_owner(claims) if request.document_ids else None
    if request.document_ids:
        query = document_query(request)
    metrics = active_metrics.get()
    if metrics:
        metrics.begin(claims.get("user_id"), "document_rag" if request.document_ids else "general_chat")

    return StreamingResponse(
        stream_chat(query, claims["sub"], owner=owner, document_ids=request.document_ids),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
