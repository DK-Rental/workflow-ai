"""
ai_search.py
Vector search path for Joe Workflow — embedding → Azure Search → GPT.

FIX 1: Used wrong OpenAI client. `from openai import OpenAI` is the standard
        client; it silently ignores Azure-specific kwargs (azure_endpoint,
        api_version) and routes all traffic to api.openai.com instead of your
        Azure resource. Every call would fail with an auth error or bill the
        wrong account. Fixed to use AzureOpenAI.

FIX 2: Vector query was a raw dict. The Azure Search SDK validates payloads
        through its own types; a plain dict bypasses that and can produce
        confusing 400 errors with no clear cause. Fixed to use VectorizedQuery.

FIX 3: No timeouts on embedding or completion calls — a hung request would
        block the Flask worker thread forever with no recovery.

FIX 4: Result extraction hardcoded the field name "content". If the index uses
        "chunk" or "text", every result silently returns "" and Joe says
        "Not in SOP" for everything. Made configurable via env var, with a
        warning log when a result has no matching field.

FIX 5: No retry on the completion call — a single 429 or 503 failed the whole
        request. Added tenacity retry with exponential backoff.

FIX 6: SOP context was mixed into the user message alongside the question,
        making the grounding boundary fuzzy. Injected as a separate system
        message so the model clearly treats it as ground-truth evidence.
"""

import logging
import os

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery  # FIX 2
from openai import AzureOpenAI  # FIX 1
from tenacity import retry, stop_after_attempt, wait_exponential

# ----------------------------
# Config
# ----------------------------

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
AZURE_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "")

AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
AZURE_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "sop")
AZURE_SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")

# FIX 4: configurable field name — set if your index uses something other than "content"
SEARCH_CONTENT_FIELD = os.getenv("AZURE_SEARCH_CONTENT_FIELD", "content")

_TIMEOUT = 20  # seconds — FIX 3

# ----------------------------
# Clients — FIX 1: AzureOpenAI, not OpenAI
# ----------------------------

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


# ----------------------------
# Helpers
# ----------------------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8), reraise=True)
def _get_embedding(text: str) -> list:
    """FIX 3: timeout added. FIX 5: retried on transient errors."""
    return _openai_client.embeddings.create(
        model=AZURE_EMBEDDING_DEPLOYMENT,
        input=text,
        timeout=_TIMEOUT,
    ).data[0].embedding


def _vector_search(embedding: list, top_k: int = 3) -> list:
    """
    FIX 2: VectorizedQuery SDK type instead of raw dict.
    FIX 4: reads SEARCH_CONTENT_FIELD; warns when field is missing.
    """
    results = _search_client.search(
        search_text="",
        vector_queries=[
            VectorizedQuery(
                vector=embedding,
                k_nearest_neighbors=top_k,
                fields="embedding",
            )
        ],
    )

    chunks = []
    for r in results:
        text = r.get(SEARCH_CONTENT_FIELD, "")
        if not text:
            # FIX 4: surface misconfigured field name immediately
            logging.warning(
                "Search result missing field '%s'. Available keys: %s",
                SEARCH_CONTENT_FIELD,
                list(r.keys()),
            )
            continue
        chunks.append(text)

    return chunks[:top_k]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8), reraise=True)
def _chat_completion(context: str, question: str) -> str:
    """
    FIX 5: retried on transient errors.
    FIX 6: context injected as a second system message, not mixed into the
           user turn — keeps the grounding boundary unambiguous.
    FIX 3: timeout added.
    """
    return _openai_client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are Joe, the SOP-only assistant for DK Rentals.\n"
                    "Answer ONLY using the SOP content provided below.\n"
                    "If the SOP does not contain the answer, say exactly: 'Not in SOP'.\n"
                    "Give precise, numbered, actionable steps."
                ),
            },
            # FIX 6: context as its own system message — clearly scoped as evidence
            {
                "role": "system",
                "content": f"SOP CONTENT:\n{context}",
            },
            {
                "role": "user",
                "content": question,
            },
        ],
        temperature=0.1,
        timeout=_TIMEOUT,
    ).choices[0].message.content or ""


# ----------------------------
# Public interface
# ----------------------------

def ask_ai(question: str) -> dict:
    """
    Answer a question via embedding → vector search → GPT.

    Returns:
        {"answer": str, "evidence": list[str]}
    """
    question = (question or "").strip()
    if not question:
        return {"answer": "Please ask a question.", "evidence": []}

    embedding = _get_embedding(question)
    docs = _vector_search(embedding)

    if not docs:
        logging.warning("No SOP chunks returned for: %s", question)
        return {"answer": "Not in SOP.", "evidence": []}

    context = "\n\n---\n\n".join(docs)
    answer = _chat_completion(context, question)

    logging.info("[ASK_AI] Q: %s | A: %s", question, answer)
    return {"answer": answer, "evidence": docs}