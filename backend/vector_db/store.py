"""
Vector database abstraction supporting ChromaDB and FAISS.
"""
from typing import List, Dict, Any, Optional, Tuple
import logging
import os

from config import settings

logger = logging.getLogger(__name__)


class VectorStore:
    """Unified interface for vector databases"""

    def __init__(self, backend: str = "chroma", collection_name: str = "default"):
        self.backend = backend
        self.collection_name = collection_name
        self._store = None
        self._init_store()

    def _init_store(self):
        if self.backend == "chroma":
            self._init_chroma()
        elif self.backend == "faiss":
            self._init_faiss()

    def _init_chroma(self):
        from services.chroma_service import ChromaManager
        self._client = ChromaManager.get_client()
        
        # get_or_create_collection can also trigger database lock, run with sync retry loop
        max_attempts = 5
        delay = 2.0
        import time
        for attempt in range(max_attempts):
            try:
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info(f"ChromaDB collection '{self.collection_name}' ready")
                return
            except Exception as e:
                err_msg = str(e).lower()
                if "database is locked" in err_msg or "db is locked" in err_msg or "code: 5" in err_msg or "locked" in err_msg:
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"ChromaDB collection init locked (attempt {attempt + 1}/{max_attempts}). "
                            f"Retrying in {delay} seconds..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(f"ChromaDB collection init locked. Max attempts reached. Error: {e}")
                        raise
                else:
                    raise

    def _init_faiss(self):
        try:
            import faiss
            import numpy as np
            self._dimension = 384
            self._index = faiss.IndexFlatL2(self._dimension)
            self._docs: List[Dict] = []
            logger.info("FAISS index ready")
        except ImportError:
            logger.warning("FAISS not installed; using mock")
            self._index = None

    async def add_documents(
        self,
        documents: List[str],
        embeddings: List[List[float]],
        metadatas: Optional[List[Dict]] = None,
        ids: Optional[List[str]] = None,
    ) -> int:
        if not ids:
            ids = [f"doc_{i}" for i in range(len(documents))]
        if not metadatas:
            metadatas = [{} for _ in documents]

        if self.backend == "chroma" and self._collection:
            from services.chroma_service import run_with_retry_async
            def op():
                self._collection.add(
                    documents=documents,
                    embeddings=embeddings,
                    metadatas=metadatas,
                    ids=ids,
                )
            await run_with_retry_async(op)
        elif self.backend == "faiss" and self._index is not None:
            import numpy as np
            arr = np.array(embeddings, dtype="float32")
            self._index.add(arr)
            self._docs.extend(
                [{"id": ids[i], "document": documents[i], "metadata": metadatas[i]}
                 for i in range(len(documents))]
            )
        return len(documents)

    async def query(
        self,
        query_embedding: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        if self.backend == "chroma" and self._collection:
            from services.chroma_service import run_with_retry_async
            def op():
                return self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=top_k,
                )
            results = await run_with_retry_async(op)
            out = []
            for i in range(len(results["ids"][0])):
                out.append({
                    "id": results["ids"][0][i],
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "score": 1 - results["distances"][0][i],  # cosine → similarity
                })
            return out

        elif self.backend == "faiss" and self._index is not None:
            import numpy as np
            arr = np.array([query_embedding], dtype="float32")
            D, I = self._index.search(arr, top_k)
            out = []
            for idx, dist in zip(I[0], D[0]):
                if idx < len(self._docs):
                    doc = self._docs[idx]
                    out.append({**doc, "score": float(1 / (1 + dist))})
            return out

        return []

    async def delete(self, ids: List[str]):
        if self.backend == "chroma" and self._collection:
            from services.chroma_service import run_with_retry_async
            def op():
                self._collection.delete(ids=ids)
            await run_with_retry_async(op)

    async def count(self) -> int:
        if self.backend == "chroma" and self._collection:
            from services.chroma_service import run_with_retry_async
            return await run_with_retry_async(self._collection.count)
        elif self.backend == "faiss" and self._index:
            return self._index.ntotal
        return 0


class MockVectorCollection:
    """In-memory mock for testing without ChromaDB"""
    _shared_collections = {}

    def __init__(self, collection_name: str = "default"):
        self.collection_name = collection_name
        if collection_name not in MockVectorCollection._shared_collections:
            MockVectorCollection._shared_collections[collection_name] = []
        self._data = MockVectorCollection._shared_collections[collection_name]

    def add(self, documents, embeddings, metadatas, ids):
        for i in range(len(ids)):
            self._data.append({
                "id": ids[i],
                "document": documents[i],
                "embedding": embeddings[i],
                "metadata": metadatas[i],
            })

    def query(self, query_embeddings, n_results=5):
        import random
        n = min(n_results, len(self._data))
        sample = self._data[:n] if self._data else []
        return {
            "ids": [[d["id"] for d in sample]],
            "documents": [[d["document"] for d in sample]],
            "metadatas": [[d["metadata"] for d in sample]],
            "distances": [[random.uniform(0.05, 0.4) for _ in sample]],
        }

    def delete(self, ids):
        self._data = [d for d in self._data if d["id"] not in ids]

    def count(self):
        return len(self._data)


def get_embedding_model(model_name: str = "all-MiniLM-L6-v2"):
    """Load sentence-transformers embedding model"""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name)


class MockEmbedder:
    def encode(self, texts, **kwargs):
        import random
        if isinstance(texts, str):
            return [random.uniform(-1, 1) for _ in range(384)]
        return [[random.uniform(-1, 1) for _ in range(384)] for _ in texts]
