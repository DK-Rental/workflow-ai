import os
from typing import List, Dict, Any, Optional

from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

# ----------------------------
# Environment / config
# ----------------------------

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "").strip()

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip()
SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "").strip()
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "").strip()
SEARCH_AUTH = (os.getenv("AZURE_SEARCH_AUTH") or "managed").lower()

COG_SERVICES_RESOURCE = os.getenv(
    "AZURE_COGNITIVE_SERVICES_RESOURCE",
    "https://cognitiveservices.azure.com",
).rstrip("/")


# ----------------------------
# Client factory
# ----------------------------

def _make_client() -> AzureOpenAI:
    if not AZURE_OPENAI_ENDPOINT:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set.")
    if not AZURE_OPENAI_DEPLOYMENT:
        raise RuntimeError("AZURE_OPENAI_DEPLOYMENT is not set.")

    if AZURE_OPENAI_API_KEY:
        return AzureOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_API_KEY,
            api_version="2025-01-01-preview",
        )

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        f"{COG_SERVICES_RESOURCE}/.default",
    )
    return AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        azure_ad_token_provider=token_provider,
        api_version="2025-01-01-preview",
    )


client = _make_client()


# ----------------------------
# Helpers
# ----------------------------

def _has_search() -> bool:
    return bool(SEARCH_ENDPOINT and SEARCH_INDEX)


def _data_source_block() -> Dict[str, Any]:
    if SEARCH_AUTH == "key":
        auth_block = {
            "type": "api_key",
            "key": SEARCH_KEY,
        }
    else:
        auth_block = {"type": "system_assigned_managed_identity"}

    return {
        "type": "azure_search",
        "parameters": {
            "endpoint": SEARCH_ENDPOINT,
            "index_name": SEARCH_INDEX,
            "in_scope": True,
            "top_n_documents": 5,
            "authentication": auth_block,
        },
    }


# ----------------------------
# MODE DETECTION
# ----------------------------

def _is_sop_question(question: str) -> bool:
    q = question.lower()
    return any(k in q for k in ["tenant", "lease", "move-in", "rent", "yardi", "application", "agreement"])


def _is_email_question(question: str) -> bool:
    q = question.lower()
    return any(w in q for w in ["email", "inbox", "message", "reply"])


# ----------------------------
# Prompt
# ----------------------------

_SYSTEM_PROMPT = (
    "You are a helpful AI assistant for a property management company.\n"
    "Answer clearly and concisely.\n"
    "Use knowledge base results when relevant.\n"
    "If unsure, say you are not sure.\n"
)


# ----------------------------
# Email formatting
# ----------------------------

def _format_emails_for_context(emails: Optional[List[Dict[str, Any]]], max_emails: int = 5) -> str:
    if not emails:
        return "No recent emails are provided."

    lines: List[str] = []

    for idx, m in enumerate(emails[:max_emails], start=1):
        subject = (m.get("subject") or "").strip()
        preview = (m.get("bodyPreview") or "").strip()
        received = m.get("receivedDateTime") or ""
        sender = m.get("sender") or {}

        sender_name = ""
        if isinstance(sender, dict):
            sender_email = sender.get("emailAddress", {}) or {}
            sender_name = sender_email.get("name") or sender_email.get("address") or ""
        elif isinstance(sender, str):
            sender_name = sender

        lines.append(
            f"{idx}. Subject: {subject}\n"
            f"   From: {sender_name}\n"
            f"   Received: {received}\n"
            f"   Snippet: {preview}"
        )

    return "Recent email snippets (use only if relevant):\n\n" + "\n\n".join(lines)


# ----------------------------
# MAIN FUNCTION
# ----------------------------

def ask_llm(question: str, emails: Optional[List[Dict[str, Any]]]) -> str:
    question = (question or "").strip()

    # ✅ Use email context ONLY for email questions
    if emails and _is_email_question(question):
        email_context_text = _format_emails_for_context(emails)
    else:
        email_context_text = "No email context."

    user_content = (
        f"User question:\n{question}\n\n"
        f"Context:\n{email_context_text}\n"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    extra_body: Dict[str, Any] = {}

    # Use Azure Search ONLY for SOP questions
    if _has_search() and _is_sop_question(question):
        ds = _data_source_block()


        extra_body["data_sources"] = [ds]

    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=messages,
        temperature=0.2,
        extra_body=extra_body or None,
    )

    return response.choices[0].message.content or ""