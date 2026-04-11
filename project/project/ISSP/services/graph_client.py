import requests
from typing import List, Dict, Any, Optional, Tuple, Iterator
from collections import deque
from urllib.parse import quote
import re

from config import (
    TENANT_ID,
    CLIENT_ID,
    CLIENT_SECRET,
    SHARED_MAILBOX,
    ONEDRIVE_USER_UPN,
    ONEDRIVE_DRIVE_ID,
)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ---------------------------
# Auth helpers
# ---------------------------

def get_graph_token() -> str:
    """
    Get an application (client credentials) access token for Microsoft Graph.
    """
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    resp = requests.post(url, data=data)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _auth_headers() -> Dict[str, str]:
    """
    Return authorization headers for Microsoft Graph using the app token.
    """
    return {"Authorization": f"Bearer {get_graph_token()}"}


# ---------------------------
# Email helpers (shared mailbox)
# ---------------------------

def get_recent_emails_for_context(
    user_question: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Fetch recent or question-relevant emails from the shared mailbox for context.

    Behavior:
      - If SHARED_MAILBOX is not configured, returns [].
      - If user_question is non-empty, first tries Graph $search for that text.
      - If search is not available or fails, falls back to the most recent messages.

    Returns a simplified list of messages with:
      subject, sender, receivedDateTime, bodyPreview
    """
    if not SHARED_MAILBOX:
        return []

    token = get_graph_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "ConsistencyLevel": "eventual",
        "Prefer": 'outlook.body-content-type="text"',
    }

    # Try search-based context first (more relevant than just "latest N")
    if user_question:
        try:
            url = f"{GRAPH_BASE}/users/{SHARED_MAILBOX}/messages"
            params = {
                "$search": f"\"{user_question}\"",
                "$top": str(limit),
                "$select": "subject,receivedDateTime,from,bodyPreview",
            }
            resp = requests.get(url, headers=headers, params=params)
            if resp.status_code == 200:
                data = resp.json().get("value", [])
                return [
                    {
                        "subject": item.get("subject"),
                        "sender": item.get("from", {}),
                        "receivedDateTime": item.get("receivedDateTime"),
                        "bodyPreview": item.get("bodyPreview"),
                    }
                    for item in data
                ]
        except Exception:
            # If search fails for any reason, we fall back to "latest emails"
            pass

    # Fallback: just return most recent messages
    url = f"{GRAPH_BASE}/users/{SHARED_MAILBOX}/messages"
    params = {
        "$top": str(limit),
        "$orderby": "receivedDateTime DESC",
        "$select": "subject,receivedDateTime,from,bodyPreview",
    }
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()

    simplified: List[Dict[str, Any]] = []
    for item in data.get("value", []):
        simplified.append(
            {
                "subject": item.get("subject"),
                "sender": item.get("from", {}),
                "receivedDateTime": item.get("receivedDateTime"),
                "bodyPreview": item.get("bodyPreview"),
            }
        )
    return simplified


def search_shared_mailbox_messages(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search the shared mailbox for messages matching the query string.

    Primary path:
      - Uses Graph $search for the query (subject + bodyPreview).
    Fallback:
      - Pulls recent messages and does a simple text match in Python.

    Returns a list of:
      subject, sender, receivedDateTime, bodyPreview, webLink
    """
    if not SHARED_MAILBOX or not query:
        return []

    token = get_graph_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "ConsistencyLevel": "eventual",
        "Prefer": 'outlook.body-content-type="text"',
    }

    url = f"{GRAPH_BASE}/users/{SHARED_MAILBOX}/messages"
    params = {
        "$search": f"\"{query}\"",
        "$top": str(limit),
        "$select": "subject,receivedDateTime,from,webLink,bodyPreview",
    }
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code == 200:
        data = resp.json().get("value", [])
        return [
            {
                "subject": m.get("subject"),
                "sender": m.get("from", {}),
                "receivedDateTime": m.get("receivedDateTime"),
                "bodyPreview": m.get("bodyPreview"),
                "webLink": m.get("webLink"),
            }
            for m in data
        ]

    # Fallback local filtering on recent messages
    recent = get_recent_emails_for_context(query, limit=50)
    q = query.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", q) if t]
    hits: List[Dict[str, Any]] = []
    for m in recent:
        text = f"{m.get('subject','')} {m.get('bodyPreview','')}".lower()
        if q in text or any(tok in text for tok in tokens):
            hits.append(
                {
                    "subject": m.get("subject"),
                    "sender": m.get("sender", {}),
                    "receivedDateTime": m.get("receivedDateTime"),
                    "bodyPreview": m.get("bodyPreview"),
                    "webLink": None,
                }
            )
            if len(hits) >= limit:
                break
    return hits


# ---------------------------
# OneDrive helpers
# ---------------------------

def resolve_user_drive_id(user_upn: str) -> str:
    """
    Resolve the OneDrive drive ID for a given user UPN.
    """
    if not user_upn:
        raise ValueError("resolve_user_drive_id: user_upn is required")

    url = f"{GRAPH_BASE}/users/{user_upn}/drive"
    resp = requests.get(url, headers=_auth_headers())
    if resp.status_code == 404:
        raise RuntimeError(
            f"OneDrive not found for user '{user_upn}'. "
            "Make sure it's a licensed user with OneDrive provisioned."
        )
    resp.raise_for_status()
    return resp.json()["id"]


def get_user_drive_id() -> str:
    """
    Return the drive ID to use for OneDrive operations.

    Priority:
      1. Explicit ONEDRIVE_DRIVE_ID from config
      2. Resolve from ONEDRIVE_USER_UPN via Graph
    """
    if ONEDRIVE_DRIVE_ID:
        return ONEDRIVE_DRIVE_ID
    if not ONEDRIVE_USER_UPN:
        raise ValueError("ONEDRIVE_USER_UPN is not set.")
    return resolve_user_drive_id(ONEDRIVE_USER_UPN)


def list_children_page(
    drive_id: str,
    parent_item_id: Optional[str] = None,
    top: int = 200,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    List one page of children for a given drive item (or root if parent_item_id is None).
    Returns (items, next_link).
    """
    if parent_item_id:
        url = f"{GRAPH_BASE}/drives/{drive_id}/items/{parent_item_id}/children"
    else:
        url = f"{GRAPH_BASE}/drives/{drive_id}/root/children"

    params = {"$top": str(top)}
    resp = requests.get(url, headers=_auth_headers(), params=params)
    resp.raise_for_status()
    data = resp.json()
    return data.get("value", []), data.get("@odata.nextLink")


def list_children_paginated(
    drive_id: str,
    parent_item_id: Optional[str] = None,
    top: int = 200,
) -> Iterator[Dict[str, Any]]:
    """
    Yield all children for a given drive item (or root) across all pages.
    """
    items, next_link = list_children_page(drive_id, parent_item_id, top)
    for it in items:
        yield it

    while next_link:
        resp = requests.get(next_link, headers=_auth_headers())
        resp.raise_for_status()
        data = resp.json()
        for it in data.get("value", []):
            yield it
        next_link = data.get("@odata.nextLink")


def list_all_files_recursive(
    drive_id: str,
    start_item_id: Optional[str] = None,
    max_files: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    """
    Breadth-first traversal of a OneDrive drive, yielding file items.
    Folders are traversed; files are yielded.
    """
    yielded = 0
    q: deque[Tuple[Optional[str], Optional[Dict[str, Any]]]] = deque([(start_item_id, None)])

    while q:
        parent_id, _ = q.popleft()
        for child in list_children_paginated(drive_id, parent_id):
            if "folder" in child:
                q.append((child["id"], child))
            else:
                yield child
                if max_files is not None:
                    yielded += 1
                    if yielded >= max_files:
                        return


def list_all_items_index(
    drive_id: str,
    max_files: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Return a lightweight index of files in a OneDrive drive, up to max_files.
    """
    out: List[Dict[str, Any]] = []
    for f in list_all_files_recursive(drive_id, max_files=max_files):
        out.append(
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "size": f.get("size"),
                "lastModifiedDateTime": f.get("lastModifiedDateTime"),
                "webUrl": f.get("webUrl"),
                "parentReference": f.get("parentReference", {}),
                "file": f.get("file", {}),
            }
        )
    return out


def get_item_by_path(drive_id: str, path: str) -> Dict[str, Any]:
    """
    Resolve a OneDrive item by its path relative to the drive root.
    """
    if not path:
        raise ValueError("path is required")
    clean = path if path.startswith("/") else f"/{path}"
    url = f"{GRAPH_BASE}/drives/{drive_id}/root:{clean}"
    resp = requests.get(url, headers=_auth_headers())
    resp.raise_for_status()
    return resp.json()


def download_item_content(drive_id: str, item_id: str) -> bytes:
    """
    Download the raw content of a OneDrive item by ID.
    """
    if not item_id:
        raise ValueError("item_id is required")
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
    resp = requests.get(url, headers=_auth_headers(), stream=True)
    resp.raise_for_status()
    return resp.content


def _safe_search(drive_id: str, query_text: str) -> List[Dict[str, Any]]:
    """
    Wrapper for the OneDrive root search API.
    """
    if not query_text:
        return []
    q = quote(query_text, safe="")
    url = f"{GRAPH_BASE}/drives/{drive_id}/root/search(q='{q}')"
    resp = requests.get(url, headers=_auth_headers())
    resp.raise_for_status()
    return resp.json().get("value", [])


def _extract_strong_tokens(queries: List[str]) -> List[str]:
    """
    Turn queries like 'where is b-1209 renewal contract' into strong tokens:
      ['b-1209', 'renewal', 'contract']

    We keep tokens that:
      - are not in a small stopword list, and
      - either contain a digit or are length >= 4
    """
    stopwords = {
        "where", "is", "the", "a", "an", "of", "in", "on", "for",
        "to", "and", "or", "do", "we", "have", "any", "file", "files",
        "document", "documents", "doc", "pdf",
    }
    tokens: List[str] = []
    for q in (queries or []):
        for tok in re.split(r"[^a-zA-Z0-9\-]+", q.lower()):
            tok = tok.strip()
            if not tok:
                continue
            if tok in stopwords:
                continue
            if any(c.isdigit() for c in tok) or len(tok) >= 4:
                tokens.append(tok)
    return tokens


def find_files_in_onedrive(
    queries: List[str],
    exts: Optional[List[str]] = None,
    max_hits: int = 10,
    fallback_scan: bool = True,
    max_scan_files: int = 3000,
) -> List[Dict[str, Any]]:
    """
    High-level OneDrive finder.

    Inputs:
      - queries: user question and any derived search keywords
      - exts: optional file extensions to filter on (e.g. ['.pdf', '.docx'])
      - max_hits: stop after this many matches
      - fallback_scan: if search returns nothing, optionally scan the drive index
      - max_scan_files: cap how many files to inspect in fallback mode

    Returns a list of dicts:
      id, name, webUrl, size, lastModifiedDateTime, parentPath
    """
    drive_id = get_user_drive_id()
    seen = set()
    results: List[Dict[str, Any]] = []

    strong_tokens = _extract_strong_tokens(queries)

    # 1) FAST GRAPH SEARCH
    for q in (queries or []):
        try:
            for it in _safe_search(drive_id, q):
                name = (it.get("name") or "").lower()
                parent_ref = it.get("parentReference", {}) or {}
                parent_path = parent_ref.get("path") or ""
                parent_path_l = parent_path.lower()

                if exts and not any(name.endswith(e.lower()) for e in exts):
                    continue

                if strong_tokens and not any(
                    tok in name or tok in parent_path_l for tok in strong_tokens
                ):
                    continue

                iid = it.get("id")
                if not iid or iid in seen:
                    continue
                seen.add(iid)
                results.append(
                    {
                        "id": iid,
                        "name": it.get("name"),
                        "webUrl": it.get("webUrl"),
                        "size": it.get("size"),
                        "lastModifiedDateTime": it.get("lastModifiedDateTime"),
                        "parentPath": parent_path,
                    }
                )
                if len(results) >= max_hits:
                    return results
        except Exception:
            # Ignore per-query failures; we can still try others or fallback.
            pass

    # 2) FALLBACK METADATA SCAN
    if (not results) and fallback_scan:
        idx = list_all_items_index(drive_id, max_files=max_scan_files)

        for it in idx:
            name = (it.get("name") or "").lower()
            parent_ref = it.get("parentReference", {}) or {}
            parent_path = parent_ref.get("path") or ""
            parent_path_l = parent_path.lower()

            if exts and not any(name.endswith(e.lower()) for e in exts):
                continue

            if strong_tokens and not any(
                tok in name or tok in parent_path_l for tok in strong_tokens
            ):
                continue

            iid = it.get("id")
            if not iid or iid in seen:
                continue
            seen.add(iid)
            results.append(
                {
                    "id": iid,
                    "name": it.get("name"),
                    "webUrl": it.get("webUrl"),
                    "size": it.get("size"),
                    "lastModifiedDateTime": it.get("lastModifiedDateTime"),
                    "parentPath": parent_path,
                }
            )
            if len(results) >= max_hits:
                break

    return results
