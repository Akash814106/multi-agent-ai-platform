import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pymongo.errors import PyMongoError

from backend.api.chat_stream import stream_chat
from backend.api.documents import document_http_error, router as document_router
from backend.api.models import ChatRequest
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
from backend.utils.provider_errors import classify_error, log_failure

app = FastAPI()
app.include_router(document_router)
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
)


@app.exception_handler(PyMongoError)
async def account_database_error(_request, error):
    logger.warning(
        "Account database request failed (%s, code=%s)",
        type(error).__name__,
        getattr(error, "code", None),
    )
    return JSONResponse(
        status_code=503,
        content={"detail": "Account storage is temporarily unavailable. Please try again later."},
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
    try:
        result = (
            answer_documents(document_query(request), owner, request.document_ids)
            if request.document_ids else run_workflow(request.query, current_user)
        )
    except DocumentError as error:
        raise document_http_error(error) from None
    except HTTPException:
        raise
    except Exception as error:
        log_failure("chat_workflow", error, classify_error(error))
        raise HTTPException(status_code=502, detail="The workflow could not finish. Please retry.") from None

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

    return StreamingResponse(
        stream_chat(query, claims["sub"], owner=owner, document_ids=request.document_ids),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
