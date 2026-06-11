# Custom AI API & API Key Management System with RAG

This document provides a complete production-grade system blueprint and code implementation for building a custom FastAPI application that exposes RAG (Retrieval-Augmented Generation) inference endpoints secured by an API Key management system.

---

## 1. Directory Structure

Here is the recommended folder layout for the system:

```text
rag-api-studio/
├── main.py                 # FastAPI application entrypoint
├── config.py               # Settings and env variables loading
├── database.py             # MongoDB connection and helper clients
├── models.py               # Pydantic schemas (User, APIKey, Dataset)
├── middleware/
│   └── auth.py             # API Key and JWT authentication middleware
├── services/
│   ├── vector_store.py     # ChromaDB / FAISS interface
│   ├── dataset_service.py  # Text extraction and chunking service
│   └── llm_service.py      # LLM query connector (OpenAI / Ollama)
└── routes/
    ├── auth.py             # User register/login endpoints
    ├── api_keys.py         # API Key creation/revocation routes
    ├── datasets.py         # Ingestion and chunking endpoints
    └── query.py            # RAG search and chat query routes
```

---

## 2. Pydantic Models & Schemas

**File**: `models.py`
This module defines the validation models for users, dataset metadata, and API key configurations.

```python
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

# --- Auth ---
class UserRegister(BaseModel):
    name: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

# --- API Keys ---
class ApiKeyCreate(BaseModel):
    name: str
    scopes: List[str] = Field(default=["chat"])
    rate_limit: int = Field(default=1000, description="Monthly request allowance")
    expires_in_days: Optional[int] = None

class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    key: Optional[str] = None  # Only populated on creation
    scopes: List[str]
    rate_limit: int
    requests_count: int
    status: str
    created_at: datetime
    expires_at: Optional[datetime] = None

# --- Ingestion & RAG ---
class QueryRequest(BaseModel):
    question: str
    dataset_id: str
    top_k: int = 5
```

---

## 3. Database Connection & Client

**File**: `database.py`
Configures asynchronous MongoDB access using `motor` and wraps collections.

```python
import motor.motor_asyncio
from os import environ

MONGO_URL = environ.get("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME = environ.get("MONGODB_DB_NAME", "rag_studio")

client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

def get_db():
    return db

async def create_indexes():
    # Enforce unique indexing on key hashes for fast auth lookups
    await db.api_keys.create_index("key_hash", unique=True)
    await db.users.create_index("email", unique=True)
```

---

## 4. API Key Generator Utility

**File**: `utils/security.py`
Generates a cryptographically secure URL-safe token. To prevent security leaks, only the prefix is exposed in lists, and only the SHA-256 hash is saved to the database.

```python
import secrets
import hashlib
from typing import Tuple

def generate_api_key_pair() -> Tuple[str, str, str]:
    """Generates (raw_key, prefix, key_hash)"""
    raw_key = f"sk-{secrets.token_urlsafe(32)}"
    prefix = raw_key[:8]  # e.g., sk-A1b2C
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, prefix, key_hash
```

---

## 5. Authentication Middleware

**File**: `middleware/auth.py`
Intercepts incoming requests, extracts API keys or JWT tokens from the `Authorization` header, validates status/limits, and updates logging parameters directly in the database.

```python
import logging
import hashlib
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from database import get_db

logger = logging.getLogger(__name__)

class ApiKeyAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path
        
        # Bypass public paths
        if path in ["/", "/api/health", "/api/v1/auth/login", "/api/v1/auth/register"]:
            await self.app(scope, receive, send)
            return

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            response = JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"}
            )
            await response(scope, receive, send)
            return

        token = auth_header.split(" ")[1]
        
        # Validate API Key
        if token.startswith("sk-"):
            db = get_db()
            key_hash = hashlib.sha256(token.encode()).hexdigest()
            key_doc = await db.api_keys.find_one({"key_hash": key_hash, "is_active": True})
            
            if not key_doc:
                response = JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid or inactive API Key"}
                )
                await response(scope, receive, send)
                return

            # Check monthly limits
            if key_doc.get("requests_count", 0) >= key_doc.get("rate_limit", 1000):
                response = JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Rate limit exceeded for this API key"}
                )
                await response(scope, receive, send)
                return

            # Update metrics
            await db.api_keys.update_one(
                {"_id": key_doc["_id"]},
                {"$inc": {"requests_count": 1}}
            )
            
            # Inject user information into request state
            scope["state"] = scope.get("state", {})
            scope["state"]["user_id"] = key_doc["user_id"]
            scope["state"]["scopes"] = key_doc["scopes"]
            
        else:
            # Fallback for JWT validation if applicable
            # (decode_jwt_token logic can go here)
            pass

        await self.app(scope, receive, send)
```

---

## 6. RAG Ingestion & Chunking Service

**File**: `services/dataset_service.py`
Parses CSV, JSON, or text documents, extracts semantic blocks, and registers them in ChromaDB.

```python
import os
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from typing import List, Dict

# Singleton ChromaDB and Embedding model initialization
chroma_client = chromadb.PersistentClient(path="./chroma_db")
embedder = SentenceTransformer("paraphrase-MiniLM-L3-v2")

async def process_and_index_dataset(dataset_id: str, file_content: str, filename: str):
    # 1. Chunk Text
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)
    chunks = text_splitter.split_text(file_content)

    if not chunks:
        raise ValueError("No text content could be extracted.")

    # 2. Generate Embeddings
    embeddings = embedder.encode(chunks).tolist()

    # 3. Insert into ChromaDB
    collection = chroma_client.get_or_create_collection(
        name=f"dataset_{dataset_id}",
        metadata={"hnsw:space": "cosine"}
    )
    
    ids = [f"{dataset_id}_{i}" for i in range(len(chunks))]
    metadatas = [{"source": filename, "chunk_index": i} for i in range(len(chunks))]

    collection.add(
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas,
        ids=ids
    )
    return len(chunks)
```

---

## 7. API Routing Enpoints

### API Key Operations (`routes/api_keys.py`)
```python
from fastapi import APIRouter, Request, Depends, HTTPException, status
from bson import ObjectId
from database import get_db
from models import ApiKeyCreate, ApiKeyResponse
from utils.security import generate_api_key_pair
from datetime import datetime

router = APIRouter(prefix="/api-keys", tags=["API Keys"])

@router.post("", response_model=ApiKeyResponse)
async def create_key(data: ApiKeyCreate, request: Request):
    user_id = request.state.user_id # populated by middleware
    raw_key, prefix, key_hash = generate_api_key_pair()
    
    doc = {
        "user_id": user_id,
        "name": data.name,
        "key_prefix": prefix,
        "key_hash": key_hash,
        "scopes": data.scopes,
        "rate_limit": data.rate_limit,
        "requests_count": 0,
        "is_active": True,
        "created_at": datetime.utcnow()
    }
    
    db = get_db()
    result = await db.api_keys.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    doc["key"] = raw_key  # raw key displayed ONLY once
    
    return ApiKeyResponse(
        id=doc["_id"],
        name=doc["name"],
        key_prefix=doc["key_prefix"],
        key=doc["key"],
        scopes=doc["scopes"],
        rate_limit=doc["rate_limit"],
        requests_count=doc["requests_count"],
        status="active",
        created_at=doc["created_at"]
    )
```

### RAG Inference Query Router (`routes/query.py`)
```python
from fastapi import APIRouter, Request, HTTPException, status
from services.dataset_service import chroma_client, embedder
from openai import OpenAI
from models import QueryRequest

router = APIRouter(prefix="/query", tags=["RAG Query"])
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

@router.post("")
async def query_dataset(data: QueryRequest, request: Request):
    # Verify scopes
    if "chat" not in request.state.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key lacks 'chat' scope authorization."
        )

    # 1. Search vector database for context
    try:
        collection = chroma_client.get_collection(name=f"dataset_{data.dataset_id}")
        query_vector = embedder.encode([data.question]).tolist()
        results = collection.query(
            query_embeddings=query_vector,
            n_results=data.top_k
        )
        context_docs = results.get("documents", [[]])[0]
        context = "\n".join(context_docs)
    except Exception as e:
        raise HTTPException(status_code=404, detail="Dataset index not found or is empty.")

    # 2. Query LLM (OpenAI) with Context
    prompt = f"""Use the following pieces of context to answer the question at the end.
    If you don't know the answer, just say you don't know, don't try to make up an answer.

    Context:
    {context}

    Question: {data.question}
    Answer:"""

    response = openai_client.chat.completions.create(
        model="gpt-4-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    return {
        "answer": response.choices[0].message.content,
        "sources": [{"text": doc} for doc in context_docs]
    }
```

---

## 8. Main Entrypoint

**File**: `main.py`
Initializes the FastAPI application, mounts routers, and registers the async database connection lifespan events.

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager
from database import create_indexes
from middleware.auth import ApiKeyAuthMiddleware
from routes import api_keys, query

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    await create_indexes()
    yield
    # Shutdown tasks (if any)

app = FastAPI(
    title="Custom AI RAG Studio",
    version="1.0.0",
    lifespan=lifespan
)

# Register Middleware
app.add_middleware(ApiKeyAuthMiddleware)

# Register Routes
app.include_router(api_keys.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")

@app.get("/api/health", tags=["System"])
def health_check():
    return {"status": "healthy"}
```
