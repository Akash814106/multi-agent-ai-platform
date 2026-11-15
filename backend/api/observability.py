import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path as RoutePath, Query
from pymongo.errors import PyMongoError

from backend.auth.dependencies import get_current_claims
from backend.utils.provider_errors import log_failure
from backend.utils.telemetry import metrics_collection


router = APIRouter(prefix="/observability", tags=["observability"])
Period = Literal["24h", "7d", "30d"]
PERIODS = {"24h": timedelta(days=1), "7d": timedelta(days=7), "30d": timedelta(days=30)}
BENCHMARK = Path(__file__).resolve().parents[1] / "evaluation" / "benchmark_summary.json"


def metrics_owner(claims: dict = Depends(get_current_claims)):
    owner = claims.get("user_id")
    if not isinstance(owner, str) or not owner:
        raise HTTPException(401, "Sign in again to use observability.")
    return owner


def scope(owner, period):
    return {"owner": owner, "started_at": {"$gte": datetime.now(UTC) - PERIODS[period]}}


def read_metrics(action):
    try:
        return action(metrics_collection())
    except PyMongoError as error:
        log_failure("observability_read", error, "observability_storage")
        raise HTTPException(503, "Observability data is temporarily unavailable.") from None


def public_record(record):
    return {
        "request_id": record["_id"],
        **{key: record[key] for key in (
            "workflow", "duration_ms", "status", "error_category", "agent_count",
            "retrieved_passages", "llm_calls", "complete",
        )},
        "started_at": record["started_at"].replace(tzinfo=UTC),
    }


@router.get("/summary")
def summary(period: Period = "7d", owner: str = Depends(metrics_owner)):
    def aggregate(collection):
        rows = list(collection.aggregate([
            {"$match": scope(owner, period)},
            {"$facet": {
                "outcomes": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}],
                "workflows": [{"$group": {"_id": "$workflow", "count": {"$sum": 1}}}],
                "overview": [{"$group": {
                    "_id": None, "total": {"$sum": 1}, "average_duration_ms": {"$avg": "$duration_ms"},
                    "incomplete": {"$sum": {"$cond": ["$complete", 0, 1]}},
                }}],
                "retrieval": [
                    {"$match": {"workflow": "document_rag", "retrieved_passages": {"$type": "number"}}},
                    {"$group": {
                        "_id": None, "measured": {"$sum": 1},
                        "average_passages": {"$avg": "$retrieved_passages"},
                        "empty": {"$sum": {"$cond": [{"$eq": ["$retrieved_passages", 0]}, 1, 0]}},
                    }},
                    {"$project": {"_id": 0}},
                ],
                "agents": [
                    {"$unwind": "$steps"},
                    {"$group": {
                        "_id": "$steps.name", "executions": {"$sum": 1},
                        "average_duration_ms": {"$avg": "$steps.duration_ms"},
                        "measured": {"$sum": {"$cond": [{"$eq": ["$steps.status", "incomplete"]}, 0, 1]}},
                        "failures": {"$sum": {"$cond": [{"$eq": ["$steps.status", "failed"]}, 1, 0]}},
                    }},
                    {"$sort": {"executions": -1, "_id": 1}},
                ],
                "failures": [
                    {"$match": {"error_category": {"$type": "string"}}},
                    {"$group": {"_id": "$error_category", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1, "_id": 1}},
                ],
                "step_failures": [
                    {"$unwind": "$steps"},
                    {"$match": {"steps.error_category": {"$type": "string"}}},
                    {"$group": {"_id": "$steps.error_category", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1, "_id": 1}},
                ],
            }},
        ]))
        data = rows[0]
        overview = next(iter(data["overview"]), {})
        outcomes = {row["_id"]: row["count"] for row in data["outcomes"]}
        overview.pop("_id", None)
        total = overview.get("total", 0)
        return {
            "total": total,
            "average_duration_ms": overview.get("average_duration_ms"),
            "incomplete": overview.get("incomplete", 0),
            "outcomes": outcomes,
            "success_rate": outcomes.get("success", 0) / total if total else None,
            "workflows": {row["_id"]: row["count"] for row in data["workflows"]},
            "retrieval": next((row for row in data["retrieval"] if row["measured"]), None),
            "agents": [{"name": row.pop("_id"), **row} for row in data["agents"]],
            "failures": {row["_id"]: row["count"] for row in data["failures"]},
            "step_failures": {row["_id"]: row["count"] for row in data["step_failures"]},
        }
    return read_metrics(aggregate)


@router.get("/requests")
def history(
    period: Period = "7d",
    page: Annotated[int, Query(ge=1, le=100)] = 1,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    owner: str = Depends(metrics_owner),
):
    records = read_metrics(lambda collection: list(
        collection.find(scope(owner, period), {"steps": 0})
        .sort([("started_at", -1), ("_id", -1)]).skip((page - 1) * limit).limit(limit + 1)
    ))
    return {"items": [public_record(record) for record in records[:limit]],
            "page": page, "has_more": page < 100 and len(records) > limit}


@router.get("/requests/{identifier}")
def detail(
    identifier: Annotated[str, RoutePath(pattern=r"^[a-f0-9]{32}$")],
    owner: str = Depends(metrics_owner),
):
    record = read_metrics(lambda collection: collection.find_one({"_id": identifier, "owner": owner}))
    if record is None:
        raise HTTPException(404, "Request not found.")
    return {**public_record(record), "steps": record["steps"]}


@router.get("/benchmark")
def benchmark(_owner: str = Depends(metrics_owner)):
    try:
        data = json.loads(BENCHMARK.read_text(encoding="utf-8"))
        return {"benchmark": {
            "dataset_version": data["dataset_version"], "embedding_model": data["embedding_model"],
            "top_k": data["top_k"], "evaluated_at": data.get("evaluated_at"),
            "total_cases": data["total_cases"], "passed_cases": data["passed_cases"],
            "recall_at_k": data["recall_at_k"], "precision_at_k": data["precision_at_k"],
            "hit_rate_at_k": data["hit_rate_at_k"],
        }}
    except (OSError, ValueError, KeyError, TypeError):
        return {"benchmark": None}
