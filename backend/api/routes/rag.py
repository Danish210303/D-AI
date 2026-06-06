from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from datetime import datetime
import time
import logging

from models import IndexCreate, IndexResponse, SearchRequest, SearchResponse, RAGChatRequest
from auth.utils import get_current_user
from database import get_db

router = APIRouter(prefix="/rag", tags=["RAG"])
logger = logging.getLogger(__name__)


def fmt_index(i: dict) -> dict:
    return {
        "id": str(i["_id"]),
        "name": i.get("name", ""),
        "dataset_id": i.get("dataset_id", ""),
        "embedding_model": i.get("embedding_model", ""),
        "chunk_count": i.get("chunk_count", 0),
        "status": i.get("status", "building"),
        "user_id": i.get("user_id", ""),
        "created_at": i.get("created_at", datetime.utcnow()),
    }


@router.post("/index")
async def create_index(
    data: IndexCreate,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user)
):
    db = get_db()
    doc = {
        "name": data.name,
        "dataset_id": data.dataset_id,
        "embedding_model": data.embedding_model,
        "chunk_size": data.chunk_size,
        "chunk_overlap": data.chunk_overlap,
        "index_type": data.index_type,
        "chunk_count": 0,
        "status": "building",
        "user_id": str(current_user["_id"]),
        "created_at": datetime.utcnow(),
    }
    result = await db.rag_indexes.insert_one(doc)
    index_id = str(result.inserted_id)
    doc["_id"] = index_id

    # Fetch the dataset document to run index builder
    dataset = await db.datasets.find_one({"_id": data.dataset_id})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    from services.dataset_service import build_index_for_dataset
    background_tasks.add_task(build_index_for_dataset, dataset, db)
    return fmt_index(doc)


@router.get("/indexes")
async def list_indexes(current_user=Depends(get_current_user)):
    db = get_db()
    indexes = []
    async for i in db.rag_indexes.find({"user_id": str(current_user["_id"])}):
        indexes.append(fmt_index(i))
    return indexes


@router.delete("/indexes/{index_id}")
async def delete_index(index_id: str, current_user=Depends(get_current_user)):
    db = get_db()
    await db.rag_indexes.delete_one({"_id": index_id, "user_id": str(current_user["_id"])})
    return {"message": "Index deleted"}


@router.post("/search")
async def search(data: SearchRequest, current_user=Depends(get_current_user)):
    db = get_db()
    start = time.time()

    index = await db.rag_indexes.find_one({"_id": data.index_id})
    if not index:
        raise HTTPException(status_code=404, detail="Index not found")
    if index.get("status") != "ready":
        raise HTTPException(status_code=400, detail="Index not ready")

    from services.chat_service import query_dataset_rag
    rag_res = await query_dataset_rag(data.index_id, data.query, data.top_k, db)
    results = rag_res.get("sources", [])
    latency = round((time.time() - start) * 1000, 2)

    return SearchResponse(
        results=results,
        query=data.query,
        index_id=data.index_id,
        latency_ms=latency,
    )


@router.post("/chat")
async def rag_chat(data: RAGChatRequest, current_user=Depends(get_current_user)):
    db = get_db()
    index = await db.rag_indexes.find_one({"_id": data.index_id})
    if not index:
        raise HTTPException(status_code=404, detail="Index not found")

    from services.chat_service import query_dataset_rag
    rag_res = await query_dataset_rag(data.index_id, data.question, data.top_k, db)

    return {
        "answer": rag_res["answer"],
        "sources": rag_res["sources"],
        "model": data.model,
        "tokens_used": len(rag_res["answer"].split()),
    }
