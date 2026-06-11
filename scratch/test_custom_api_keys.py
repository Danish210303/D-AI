import sys
import os
import asyncio
import httpx

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

from database import connect_db, get_db
from auth.utils import create_access_token
from main import app

async def run_tests():
    print("Connecting to DB...")
    await connect_db()
    db = get_db()
    
    # 1. Fetch or create a test user
    user = await db.users.find_one({"email": "danish@gmail.com"})
    if not user:
        print("Creating mock user danish@gmail.com for tests...")
        user = {
            "name": "Danish",
            "email": "danish@gmail.com",
            "disabled": False,
            "role": "user"
        }
        res = await db.users.insert_one(user)
        user["_id"] = res.inserted_id
        
    user_id = str(user["_id"])
    print(f"Using User: {user['email']} (ID: {user_id})")
    
    # Generate JWT token to authenticate API Key creation
    token = create_access_token({"sub": user_id, "email": user["email"]})
    
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # 2. Test creation of scoped API Key with dataset limits
        payload = {
            "name": "Scoped Test Key",
            "scopes": ["chat"],
            "rate_limit": 15,
            "dataset_ids": ["mock_dataset_allowed"],
            "model_ids": []
        }
        
        print("\n--- 1. Creating API Key with dataset restrictions ---")
        response = await client.post("/api/v1/api-keys", json=payload, headers=headers)
        assert response.status_code == 200, f"Failed to create key: {response.text}"
        data = response.json()
        
        full_key = data["key"]
        key_id = data["id"]
        prefix = data["key_prefix"]
        print(f"Created key ID: {key_id}")
        print(f"Key Prefix: {prefix}")
        print(f"Full Key: {full_key}")
        
        # Verify prefix format and prefix length
        assert full_key.startswith("sk-ai_"), f"Key should start with sk-ai_: {full_key}"
        assert len(prefix) == 11, f"Prefix should be 11 characters (sk-ai_xxxxx): {prefix}"
        print("[OK] Prefix format checks passed.")
        
        # 3. Verify API Key authorization and scope check
        print("\n--- 2. Testing scope checks using created API Key ---")
        auth_headers = {"Authorization": f"Bearer {full_key}"}
        
        # /ai/embed requires "embed" scope. Key only has "chat". Should get 403.
        embed_payload = ["hello world"]
        response = await client.post("/api/v1/ai/embed", json=embed_payload, headers=auth_headers)
        print("Embed endpoint status (expected 403):", response.status_code)
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        print("[OK] Embed scope restriction verified.")

        # /ai/summarize requires "chat" scope. Key has "chat". Should bypass scope check (might fail on Ollama, but not 403).
        sum_payload = {"text": "Hello world, this is a test text.", "max_length": 10}
        response = await client.post("/api/v1/ai/summarize", json=sum_payload, headers=auth_headers)
        print("Summarize endpoint status (expected non-403):", response.status_code)
        assert response.status_code != 403, "Summarize got 403, scope check failed!"
        print("[OK] Summarize scope verification passed.")
        
        # 4. Verify dataset restrictions
        print("\n--- 3. Testing dataset boundaries check ---")
        # Let's mock a RAG index in the database to search on.
        # We need two indexes: one linked to 'mock_dataset_allowed', and one linked to 'mock_dataset_denied'
        await db.rag_indexes.delete_many({"_id": {"$in": ["idx_allowed", "idx_denied"]}})
        
        await db.rag_indexes.insert_one({
            "_id": "idx_allowed",
            "name": "Allowed Index",
            "dataset_id": "mock_dataset_allowed",
            "status": "ready",
            "user_id": user_id
        })
        await db.rag_indexes.insert_one({
            "_id": "idx_denied",
            "name": "Denied Index",
            "dataset_id": "mock_dataset_denied",
            "status": "ready",
            "user_id": user_id
        })
        
        # Call search on DENIED index
        search_denied_payload = {"index_id": "idx_denied", "query": "hello"}
        response = await client.post("/api/v1/rag/search", json=search_denied_payload, headers=auth_headers)
        print("Search denied index status (expected 403):", response.status_code)
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        print("[OK] Dataset block verified.")

        # Call search on ALLOWED index
        search_allowed_payload = {"index_id": "idx_allowed", "query": "hello"}
        # Note: search might return other errors if Chroma collection isn't real, but it should NOT be 403!
        response = await client.post("/api/v1/rag/search", json=search_allowed_payload, headers=auth_headers)
        print("Search allowed index status (expected non-403):", response.status_code)
        assert response.status_code != 403, f"Search allowed returned 403: {response.text}"
        print("[OK] Dataset allowance verified.")
        
        # 5. Rotate key check
        print("\n--- 4. Rotating API Key ---")
        response = await client.post(f"/api/v1/api-keys/{key_id}/rotate", headers=headers)
        assert response.status_code == 200, f"Rotation failed: {response.text}"
        rot_data = response.json()
        new_full_key = rot_data["key"]
        print("Rotated key:", new_full_key)
        assert new_full_key.startswith("sk-ai_"), "Rotated key should start with sk-ai_"
        print("[OK] Key rotation format verified.")
        
        # Cleanup
        await db.rag_indexes.delete_many({"_id": {"$in": ["idx_allowed", "idx_denied"]}})
        print("\nAll tests completed successfully!")

if __name__ == '__main__':
    asyncio.run(run_tests())
