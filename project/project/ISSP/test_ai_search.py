"""
test_ai_search.py
Run this to verify your vector RAG pipeline is working end to end.

Usage:
    python test_ai_search.py
"""

import os
import sys

# Make sure .env is loaded
from dotenv import load_dotenv
load_dotenv()

# ── Check required env vars ───────────────────────────────────────────────────
REQUIRED = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_EMBEDDING_DEPLOYMENT",
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_KEY",
    "AZURE_SEARCH_INDEX",
]

print("=" * 60)
print("STEP 1 — Checking environment variables")
print("=" * 60)

missing = []
for var in REQUIRED:
    val = os.getenv(var, "")
    if val:
        print(f"  ✅ {var} = ...{val[-6:]}")
    else:
        print(f"  ❌ {var} = MISSING")
        missing.append(var)

if missing:
    print(f"\n❌ Missing {len(missing)} required variable(s). Add them to your .env file.")
    sys.exit(1)

print("\n✅ All environment variables present.\n")

# ── Test embedding ────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 2 — Testing embedding model")
print("=" * 60)

try:
    from openai import AzureOpenAI
    client = AzureOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
    )
    resp = client.embeddings.create(
        model=os.getenv("AZURE_EMBEDDING_DEPLOYMENT"),
        input="test embedding",
        timeout=15,
    )
    dims = len(resp.data[0].embedding)
    print(f"  ✅ Embedding model works — {dims} dimensions")
    if dims != 3072:
        print(f"  ⚠️  Expected 3072 dims (text-embedding-3-large) but got {dims}")
        print(f"     Make sure AZURE_EMBEDDING_DEPLOYMENT=text-embedding-3-large")
except Exception as e:
    print(f"  ❌ Embedding failed: {e}")
    sys.exit(1)

# ── Test vector search ────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 3 — Testing vector search")
print("=" * 60)

try:
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.models import VectorizedQuery

    search_client = SearchClient(
        endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
        index_name=os.getenv("AZURE_SEARCH_INDEX"),
        credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_KEY")),
    )

    embedding = resp.data[0].embedding
    results = list(search_client.search(
        search_text="",
        vector_queries=[
            VectorizedQuery(
                vector=embedding,
                k_nearest_neighbors=3,
                fields="text_vector",
            )
        ],
        select=["title", "chunk"],
        top=3,
    ))

    if results:
        print(f"  ✅ Vector search returned {len(results)} result(s)")
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            chunk = r.get("chunk", "")[:80]
            print(f"  [{i}] {title}")
            print(f"       {chunk}…")
    else:
        print("  ⚠️  Vector search returned 0 results — index may be empty")
        sys.exit(1)

except Exception as e:
    print(f"  ❌ Vector search failed: {e}")
    sys.exit(1)

# ── Test full RAG ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 4 — Testing full RAG (ask_ai)")
print("=" * 60)

questions = [
    "What is the move-in inspection process?",
    "How should I handle a maintenance request?",
    "What are the parking stall rules for tenants?",
]

try:
    from services.ai_search import ask_ai

    for q in questions:
        print(f"\n  Q: {q}")
        result = ask_ai(q)
        answer  = result.get("answer", "")
        sources = result.get("sources", [])
        print(f"  A: {answer[:200]}{'…' if len(answer) > 200 else ''}")
        if sources:
            titles = list({s.get('title','') for s in sources if s.get('title')})
            print(f"  Sources: {', '.join(titles)}")
        print()

except Exception as e:
    print(f"  ❌ ask_ai failed: {e}")
    sys.exit(1)

print("=" * 60)
print("✅ All tests passed — RAG pipeline is working!")
print("=" * 60)