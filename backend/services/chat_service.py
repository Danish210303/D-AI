import logging
from database import get_db
from vector_db.store import VectorStore, get_embedding_model
from ollama import AsyncClient
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger(__name__)

async def query_dataset_rag(index_id: str, question: str, top_k: int = 5, db = None) -> dict:
    """
    Retrieve matching context chunks from ChromaDB and answer the user question.
    Falls back to Dataset-Only RAG Mode if Ollama/OpenAI is offline.
    """
    # 1. Fetch index and dataset documents from DB
    index = await db.rag_indexes.find_one({"_id": index_id})
    if not index:
        return {"answer": "Index not found.", "sources": []}
        
    dataset_id = index.get("dataset_id")
    dataset = await db.datasets.find_one({"_id": dataset_id})
    source_name = dataset.get("file_name") or dataset.get("name", "unknown")

    # 2. Get active embedder model (tiered fallback)
    embedder = get_embedding_model(index.get("embedding_model", "all-MiniLM-L6-v2"))
    is_local_search = embedder.__class__.__name__ == "HashingTFIDFEmbedder"

    # 3. Generate query embedding
    import asyncio
    if hasattr(embedder, "encode"):
        if asyncio.iscoroutinefunction(embedder.encode):
            query_emb = await embedder.encode(question)
        else:
            query_emb = await asyncio.to_thread(embedder.encode, question)
    else:
        query_emb = []
        
    if hasattr(query_emb, "tolist"):
        query_emb = query_emb.tolist()

    # 4. Search ChromaDB persistent store
    store = VectorStore(backend="chroma", collection_name=index_id)
    raw_results = await store.query(query_emb, top_k=top_k)

    sources = []
    for r in raw_results:
        sources.append({
            "content": r["document"],
            "score": float(r["score"]),
            "source": r["metadata"].get("source", source_name),
            "metadata": r["metadata"]
        })

    # Sort sources by similarity score descending
    sources.sort(key=lambda x: x["score"], reverse=True)

    # Filter with similarity score threshold: 0.20 for TF-IDF keyword matching, 0.30 for semantic
    threshold = 0.20 if is_local_search else 0.30
    valid_sources = [s for s in sources if s["score"] >= threshold]

    if not valid_sources:
        return {
            "answer": "No relevant information found in the uploaded dataset.",
            "sources": []
        }

    best_match = valid_sources[0]
    context = "\n\n".join([s["content"] for s in valid_sources])

    prompt = f"""Use the following pieces of context to answer the user question. Keep your answer strictly based on the context. If the answer cannot be found in the context, say "No relevant information found in the uploaded dataset."

Context:
{context}

Question: {question}
Answer:"""

    answer_text = ""
    llm_connected = False

    # A: Try Ollama first
    try:
        client = AsyncClient(host=settings.OLLAMA_BASE_URL, headers={"bypass-tunnel-reminder": "true"})
        res = await client.generate(
            model=settings.DEFAULT_MODEL or "llama3",
            prompt=prompt,
            stream=False
        )
        answer_text = res.get("response", "").strip()
        if answer_text:
            llm_connected = True
    except Exception as ollama_err:
        logger.warning(f"Ollama RAG generate failed: {ollama_err}. Trying OpenAI fallback...")
        
    # B: Try OpenAI fallback if Ollama is unavailable
    if not llm_connected and settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("sk-..."):
        try:
            openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            res = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                stream=False
            )
            answer_text = res.choices[0].message.content.strip()
            if answer_text:
                llm_connected = True
        except Exception as openai_err:
            logger.error(f"OpenAI fallback failed for RAG: {openai_err}")

    # C: If both are unavailable, fall back to Dataset-Only RAG Mode
    if not llm_connected:
        # Strictly return answer from the best matching chunk retrieved from ChromaDB
        answer_text = f"[Dataset-Only RAG Active] Direct match retrieved: {best_match['content']}"

    # Format output structured display: Dataset answer, Source file name, Similarity score, Retrieved chunk
    final_display_answer = (
        f"{answer_text}\n\n"
        f"---\n"
        f"**Source File Name:** {best_match['source']}\n"
        f"**Similarity Score:** {best_match['score']:.4f}\n"
        f"**Retrieved Chunk:** {best_match['content']}"
    )

    from models import SearchResult
    pydantic_sources = [
        SearchResult(
            content=s["content"],
            score=s["score"],
            source=s["source"],
            metadata=s["metadata"]
        ) for s in valid_sources
    ]

    return {
        "answer": final_display_answer,
        "sources": pydantic_sources
    }
