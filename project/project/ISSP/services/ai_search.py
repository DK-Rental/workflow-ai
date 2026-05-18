"""
ai_search.py
Vector search RAG for Joe Workflow.

Index: rag-1762581495053
  - content field : chunk
  - vector field  : text_vector (3072 dims → text-embedding-3-large)
  - title field   : title
"""

import logging
import os

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

# ── Config ────────────────────────────────────────────────────────────────────
AZURE_OPENAI_ENDPOINT    = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY     = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
AZURE_OPENAI_DEPLOYMENT  = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")

AZURE_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
AZURE_SEARCH_KEY      = os.getenv("AZURE_SEARCH_KEY", "")
AZURE_SEARCH_INDEX    = os.getenv("AZURE_SEARCH_INDEX", "rag-1762581495053")

CONTENT_FIELD = "chunk"
VECTOR_FIELD  = "text_vector"
TITLE_FIELD   = "title"

_TIMEOUT         = 30
_HISTORY_CONTEXT = 3  # number of recent turn-pairs to pass to the LLM

# ── Clients ───────────────────────────────────────────────────────────────────
_openai_client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
)

_search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX,
    credential=AzureKeyCredential(AZURE_SEARCH_KEY),
)


# ── Embedding ─────────────────────────────────────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8), reraise=True)
def _get_embedding(text: str) -> list:
    """Embed using text-embedding-3-large (3072 dims, best quality)."""
    return _openai_client.embeddings.create(
        model=AZURE_EMBEDDING_DEPLOYMENT,
        input=text,
        timeout=_TIMEOUT,
    ).data[0].embedding


# ── Vector search ─────────────────────────────────────────────────────────────
def _vector_search(embedding: list, top_k: int = 5) -> list[dict]:
    """
    Hybrid search: vector similarity + keyword fallback.
    Returns list of {title, chunk} dicts.
    """
    results = _search_client.search(
        search_text="",
        vector_queries=[
            VectorizedQuery(
                vector=embedding,
                k_nearest_neighbors=top_k,
                fields=VECTOR_FIELD,
            )
        ],
        select=[TITLE_FIELD, CONTENT_FIELD],
        top=top_k,
    )

    chunks = []
    for r in results:
        chunk = r.get(CONTENT_FIELD, "")
        title = r.get(TITLE_FIELD, "Unknown")
        if not chunk:
            logging.warning("Result missing '%s'. Keys: %s", CONTENT_FIELD, list(r.keys()))
            continue
        chunks.append({"title": title, "chunk": chunk})

    return chunks


def _build_search_query(question: str, history: list) -> str:
    """
    Prepend the last user question to the current question before embedding.
    This means follow-ups like "does that apply to commercial tenants too?"
    get embedded with enough context to find the right SOP chunks.
    """
    if not history:
        return question

    for turn in reversed(history):
        if turn.get("role") == "user":
            prior = turn["content"]
            # Strip group-chat bystander prefix
            if prior.startswith("Team context: "):
                prior = prior[len("Team context: "):]
            return f"{prior} {question}"

    return question


# ── Completion ────────────────────────────────────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8), reraise=True)
def _chat_completion(context: str, question: str, history: list) -> str:
    """
    Call the LLM with SOP context + recent conversation history + current question.
    History is capped at the last _HISTORY_CONTEXT turn-pairs to stay within token budget.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are Joe, a senior property manager at DK Rentals.\n"
                "Answer ONLY using the SOP content provided below.\n"
                "If the answer is not in the SOP, say exactly: "
                "'I'm not sure — this is not in the SOP.'\n"
                "Use numbered steps for instructions. "
                "Bold important warnings. Be precise and professional."
            ),
        },
        {
            "role": "system",
            "content": f"SOP CONTENT:\n{context}",
        },
    ]

    # Inject recent turns so the LLM knows what "that" or "it" refers to
    if history:
        messages.extend(history[-(_HISTORY_CONTEXT * 2):])

    messages.append({"role": "user", "content": question})

    return _openai_client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=messages,
        temperature=0.1,
        timeout=_TIMEOUT,
    ).choices[0].message.content or ""


# ── Public interface ──────────────────────────────────────────────────────────
def ask_ai(question: str, history: list = None) -> dict:
    """
    Answer a question via embedding → vector search → GPT.

    Args:
        question: The current user question.
        history:  Conversation history as [{"role": ..., "content": ...}, ...]
                  Used to resolve follow-up references in both the vector search
                  query and the LLM completion.

    Returns:
        {"answer": str, "sources": [{"title": str, "chunk": str}]}
    """
    question = (question or "").strip()
    if not question:
        return {"answer": "Please ask a question.", "sources": []}

    history = history or []

    try:
        
        search_query = _build_search_query(question, history)
        embedding    = _get_embedding(search_query)
        docs         = _vector_search(embedding, top_k=5)

        if not docs:
            logging.warning("No SOP chunks returned for: %s", search_query)
            return {"answer": "I'm not sure — this is not in the SOP.", "sources": []}

        context = "\n\n---\n\n".join(
            f"[{d['title']}]\n{d['chunk']}" for d in docs
        )
        answer = _chat_completion(context, question, history)

        logging.info("[ASK_AI] Q: %s | search_query: %s | chunks: %d",
                     question, search_query, len(docs))
        return {"answer": answer, "sources": docs}

    except Exception as e:
        logging.error("ask_ai error: %s", e)
        return {"answer": "I'm having trouble accessing the SOPs right now.", "sources": []}