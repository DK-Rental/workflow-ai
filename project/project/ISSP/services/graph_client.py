"""
graph_client.py
Microsoft Graph helpers — shared mailbox email + OneDrive file search.

FIX 1: get_graph_token() was called inside every helper function — that's a full
        HTTP round-trip to Azure AD on every single call. One user question that
        touches email + OneDrive would fire 3-4 separate token requests. Added a
        simple TTL cache so the token is reused until 60 s before expiry.

FIX 2: No timeouts on any requests call — a hung Graph API response would block
        the Flask worker thread forever. Added timeout=20 everywhere.

FIX 3: search_shared_mailbox_messages fallback path read m.get("sender", {})
        but get_recent_emails_for_context returns the key as "sender" on some
        items and "from" on others (it maps item.get("from") to "sender").
        The fallback was always returning an empty dict for the sender.
        Normalised both functions to always use the key "from".

FIX 4: list_children_page and list_children_paginated were two separate functions
        where the paginated version re-fetched the first page through the single-
        page function, then followed nextLink separately — duplicated logic that
        could drift. Merged into one generator.

FIX 5: find_files_in_onedrive swallowed ALL exceptions with bare
        `except Exception: pass`, so a misconfigured drive ID or expired token
        looked identical to "no files found". Now logs every exception and
        re-raises on 401/403 auth errors immediately.

FIX 6: resolve_user_drive_id had no retry — a transient 503 on drive resolution
        crashed the entire file-search request. Added tenacity retry.
"""

import logging
import re
import time
from collections import deque
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import (
    CLIENT_ID,
    CLIENT_SECRET,
    ONEDRIVE_DRIVE_ID,
    ONEDRIVE_USER_UPN,
    SHARED_MAILBOX,
    TENANT_ID,
)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 20  # seconds — FIX 2


# ---------------------------
# Auth — FIX 1: cached token
# ---------------------------

_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0.0}


def get_graph_token() -> str:
    """Return a valid app-credential token, refreshing only when near expiry."""
    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    resp = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    _token_cache["token"] = payload["access_token"]
    _token_cache["expires_at"] = now + int(payload.get("expires_in", 3600)) - 60
    return _token_cache["token"]


def _auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {get_graph_token()}"}


# ---------------------------
# Email helpers
# ---------------------------

def get_recent_emails_for_context(
    user_question: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    FIX 2: timeout added to all requests calls.
    FIX 3: result dicts now always use the key "from" (was inconsistently
           "sender" in the original, causing empty dicts in the fallback path).
    """
    if not SHARED_MAILBOX:
        return []

    headers = {
        "Authorization": f"Bearer {get_graph_token()}",
        "ConsistencyLevel": "eventual",
        "Prefer": 'outlook.body-content-type="text"',
    }

    def _shape(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "subject": item.get("subject"),
            "from": item.get("from", {}),   # FIX 3: always "from"
            "receivedDateTime": item.get("receivedDateTime"),
            "bodyPreview": item.get("bodyPreview"),
        }

    if user_question:
        try:
            resp = requests.get(
                f"{GRAPH_BASE}/users/{SHARED_MAILBOX}/messages",
                headers=headers,
                params={
                    "$search": f'"{user_question}"',
                    "$top": str(limit),
                    "$select": "subject,receivedDateTime,from,bodyPreview",
                },
                timeout=_TIMEOUT,  # FIX 2
            )
            if resp.status_code == 200:
                return [_shape(m) for m in resp.json().get("value", [])]
        except Exception as exc:
            logging.warning("Email search failed, falling back to recent: %s", exc)

    resp = requests.get(
        f"{GRAPH_BASE}/users/{SHARED_MAILBOX}/messages",
        headers=headers,
        params={
            "$top": str(limit),
            "$orderby": "receivedDateTime DESC",
            "$select": "subject,receivedDateTime,from,bodyPreview",
        },
        timeout=_TIMEOUT,  # FIX 2
    )
    resp.raise_for_status()
    return [_shape(m) for m in resp.json().get("value", [])]


def search_shared_mailbox_messages(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    FIX 2: timeout added.
    FIX 3: fallback path now reads "from" consistently (was "sender").
    """
    if not SHARED_MAILBOX or not query:
        return []

    headers = {
        "Authorization": f"Bearer {get_graph_token()}",
        "ConsistencyLevel": "eventual",
        "Prefer": 'outlook.body-content-type="text"',
    }

    resp = requests.get(
        f"{GRAPH_BASE}/users/{SHARED_MAILBOX}/messages",
        headers=headers,
        params={
            "$search": f'"{query}"',
            "$top": str(limit),
            "$select": "subject,receivedDateTime,from,webLink,bodyPreview",
        },
        timeout=_TIMEOUT,  # FIX 2
    )

    if resp.status_code == 200:
        return [
            {
                "subject": m.get("subject"),
                "from": m.get("from", {}),   # FIX 3
                "receivedDateTime": m.get("receivedDateTime"),
                "bodyPreview": m.get("bodyPreview"),
                "webLink": m.get("webLink"),
            }
            for m in resp.json().get("value", [])
        ]

    # Fallback: local filter over recent messages
    recent = get_recent_emails_for_context(query, limit=50)
    q_lower = query.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", q_lower) if t]
    hits: List[Dict[str, Any]] = []
    for m in recent:
        text = f"{m.get('subject', '')} {m.get('bodyPreview', '')}".lower()
        if q_lower in text or any(tok in text for tok in tokens):
            hits.append({
                "subject": m.get("subject"),
                "from": m.get("from", {}),   # FIX 3: was m.get("sender", {})
                "receivedDateTime": m.get("receivedDateTime"),
                "bodyPreview": m.get("bodyPreview"),
                "webLink": None,
            })
            if len(hits) >= limit:
                break
    return hits


# ---------------------------
# OneDrive helpers
# ---------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    reraise=True,
)  # FIX 6
def resolve_user_drive_id(user_upn: str) -> str:
    if not user_upn:
        raise ValueError("resolve_user_drive_id: user_upn is required")
    resp = requests.get(
        f"{GRAPH_BASE}/users/{user_upn}/drive",
        headers=_auth_headers(),
        timeout=_TIMEOUT,  # FIX 2
    )
    if resp.status_code == 404:
        raise RuntimeError(
            f"OneDrive not found for '{user_upn}'. "
            "Make sure it's a licensed user with OneDrive provisioned."
        )
    resp.raise_for_status()
    return resp.json()["id"]


def get_user_drive_id() -> str:
    if ONEDRIVE_DRIVE_ID:
        return ONEDRIVE_DRIVE_ID
    if not ONEDRIVE_USER_UPN:
        raise ValueError("ONEDRIVE_USER_UPN is not set.")
    return resolve_user_drive_id(ONEDRIVE_USER_UPN)


def list_children_paginated(
    drive_id: str,
    parent_item_id: Optional[str] = None,
    top: int = 200,
) -> Iterator[Dict[str, Any]]:
    """
    FIX 4: Merged list_children_page + list_children_paginated into one generator.
    The original fetched the first page through a separate function then followed
    nextLink separately — duplicated logic. Now a single loop handles all pages.
    FIX 2: timeout on every page fetch.
    """
    url = (
        f"{GRAPH_BASE}/drives/{drive_id}/items/{parent_item_id}/children"
        if parent_item_id
        else f"{GRAPH_BASE}/drives/{drive_id}/root/children"
    )
    params: Dict[str, Any] = {"$top": str(top)}

    while url:
        resp = requests.get(url, headers=_auth_headers(), params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("value", []):
            yield item
        url = data.get("@odata.nextLink")
        params = {}  # nextLink already contains query params


def list_all_files_recursive(
    drive_id: str,
    start_item_id: Optional[str] = None,
    max_files: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    """Breadth-first traversal; yields file items only."""
    yielded = 0
    queue: deque = deque([start_item_id])

    while queue:
        parent_id = queue.popleft()
        for child in list_children_paginated(drive_id, parent_id):
            if "folder" in child:
                queue.append(child["id"])
            else:
                yield child
                yielded += 1
                if max_files is not None and yielded >= max_files:
                    return


def list_all_items_index(
    drive_id: str,
    max_files: Optional[int] = None,
) -> List[Dict[str, Any]]:
    return [
        {
            "id": f.get("id"),
            "name": f.get("name"),
            "size": f.get("size"),
            "lastModifiedDateTime": f.get("lastModifiedDateTime"),
            "webUrl": f.get("webUrl"),
            "parentReference": f.get("parentReference", {}),
            "file": f.get("file", {}),
        }
        for f in list_all_files_recursive(drive_id, max_files=max_files)
    ]


def get_item_by_path(drive_id: str, path: str) -> Dict[str, Any]:
    if not path:
        raise ValueError("path is required")
    clean = path if path.startswith("/") else f"/{path}"
    resp = requests.get(
        f"{GRAPH_BASE}/drives/{drive_id}/root:{clean}",
        headers=_auth_headers(),
        timeout=_TIMEOUT,  # FIX 2
    )
    resp.raise_for_status()
    return resp.json()


def download_item_content(drive_id: str, item_id: str) -> bytes:
    if not item_id:
        raise ValueError("item_id is required")
    resp = requests.get(
        f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content",
        headers=_auth_headers(),
        stream=True,
        timeout=_TIMEOUT,  # FIX 2
    )
    resp.raise_for_status()
    return resp.content


def _safe_search(drive_id: str, query_text: str) -> List[Dict[str, Any]]:
    if not query_text:
        return []
    q = quote(query_text, safe="")
    resp = requests.get(
        f"{GRAPH_BASE}/drives/{drive_id}/root/search(q='{q}')",
        headers=_auth_headers(),
        timeout=_TIMEOUT,  # FIX 2
    )
    resp.raise_for_status()
    return resp.json().get("value", [])


def _extract_strong_tokens(queries: List[str]) -> List[str]:
    stopwords = {
        "where", "is", "the", "a", "an", "of", "in", "on", "for",
        "to", "and", "or", "do", "we", "have", "any", "file", "files",
        "document", "documents", "doc", "pdf",
    }
    tokens: List[str] = []
    for q in (queries or []):
        for tok in re.split(r"[^a-zA-Z0-9\-]+", q.lower()):
            tok = tok.strip()
            if not tok or tok in stopwords:
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
    FIX 5: Original used bare `except Exception: pass` everywhere in the search
    loop — a wrong drive ID or expired token looked identical to "no results".
    Now logs every failure. Auth errors (401/403) are re-raised immediately
    since retrying them is pointless and masks a real config problem.
    """
    drive_id = get_user_drive_id()
    seen: set = set()
    results: List[Dict[str, Any]] = []
    strong_tokens = _extract_strong_tokens(queries)

    def _matches(item: Dict[str, Any]) -> bool:
        name = (item.get("name") or "").lower()
        parent_path = ((item.get("parentReference") or {}).get("path") or "").lower()
        if exts and not any(name.endswith(e.lower()) for e in exts):
            return False
        if strong_tokens and not any(tok in name or tok in parent_path for tok in strong_tokens):
            return False
        return True

    def _shape(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "webUrl": item.get("webUrl"),
            "size": item.get("size"),
            "lastModifiedDateTime": item.get("lastModifiedDateTime"),
            "parentPath": ((item.get("parentReference") or {}).get("path") or ""),
        }

    # 1) Graph search
    for q in (queries or []):
        try:
            for item in _safe_search(drive_id, q):
                if not _matches(item):
                    continue
                iid = item.get("id")
                if not iid or iid in seen:
                    continue
                seen.add(iid)
                results.append(_shape(item))
                if len(results) >= max_hits:
                    return results
        except requests.HTTPError as exc:
            # FIX 5: auth failures are config problems — surface them immediately
            if exc.response is not None and exc.response.status_code in (401, 403):
                logging.error("OneDrive auth error for query '%s': %s", q, exc)
                raise
            logging.warning("OneDrive search failed for query '%s': %s", q, exc)
        except Exception as exc:
            logging.warning("OneDrive search unexpected error for query '%s': %s", q, exc)

    # 2) Fallback metadata scan
    if not results and fallback_scan:
        for item in list_all_files_recursive(drive_id, max_files=max_scan_files):
            if not _matches(item):
                continue
            iid = item.get("id")
            if not iid or iid in seen:
                continue
            seen.add(iid)
            results.append(_shape(item))
            if len(results) >= max_hits:
                break

    return results