from flask import Blueprint, request, jsonify, render_template, redirect
import os
import re
from typing import List, Dict, Any

from services.graph_client import (
    get_recent_emails_for_context,
    find_files_in_onedrive,
)
from services.llm_client import ask_llm

# RAG (SOP AI)
try:
    from services.ai_search import ask_ai as ask_sop_ai
    _SOP_OK = True
except Exception:
    ask_sop_ai = None
    _SOP_OK = False

bp = Blueprint("api", __name__)

# ----------------------------
# UI ROUTES
# ----------------------------

@bp.route("/", methods=["GET"])
def root():
    return redirect("/chat", code=302)

@bp.route("/chat", methods=["GET"])
def chat_page():
    return render_template("index.html")

@bp.route("/api/messages", methods=["POST"])
def teams_bot():
    data = request.json or {}

    user_text = data.get("text", "")

    if not user_text:
        return jsonify({"type": "message", "text": "No input received"})

    # call your existing system
    response = ask_llm(user_text, emails=[])

    return jsonify({
        "type": "message",
        "text": response
    })

# ----------------------------
# HELPERS
# ----------------------------

def _is_file_lookup(question: str) -> bool:
    q = (question or "").lower()

    file_words = ["file", "document", "pdf", "doc", "attachment"]
    if any(w in q for w in file_words):
        return True

    if "where is" in q or "find" in q:
        return True

    return False

def _is_email_question(question: str) -> bool:
    q = (question or "").lower()
    email_words = ["email", "inbox", "message", "reply"]
    return any(w in q for w in email_words)

def _friendly_location(parent_path: str) -> str:
    if not parent_path:
        return "OneDrive root"

    if "root:" in parent_path:
        parent_path = parent_path.split("root:", 1)[1]

    return f"OneDrive /{parent_path.strip(':/')}"

# ----------------------------
# MAIN CHAT API
# ----------------------------

@bp.route("/chat", methods=["POST"])
def chat():
    payload = request.get_json(silent=True) or {}
    user_question = payload.get("user_question") or payload.get("question") or ""

    if not user_question:
        return jsonify({"error": "Missing question"}), 400

    # ----------------------------
    # FILE SEARCH MODE
    # ----------------------------
    if _is_file_lookup(user_question):
        try:
            files = find_files_in_onedrive(
                queries=[user_question],
                exts=[".pdf", ".docx", ".xlsx"],
                max_hits=5
            )
        except Exception:
            files = []

        files_view = [
            {
                "name": f["name"],
                "url": f["webUrl"],
                "location": _friendly_location(f.get("parentPath", ""))
            }
            for f in files
        ]

        if files_view:
            return jsonify({
                "answer": f"Found {len(files_view)} file(s).",
                "mode": "file_search",
                "evidence": {"files": files_view}
            })

        return jsonify({
            "answer": "No files found.",
            "mode": "file_search",
            "evidence": {}
        })

    # ----------------------------
    # SOP RAG MODE (MAIN FEATURE)
    # ----------------------------
    if _SOP_OK:
        try:
            sop_response = ask_sop_ai(user_question)

            if isinstance(sop_response, dict) and "answer" in sop_response:
                return jsonify({
                    "answer": sop_response["answer"],
                    "mode": "workflow_sop",
                    "confidence": "high",
                    "evidence": sop_response.get("evidence", {})
                })

        except Exception as e:
            print("SOP ERROR:", e)

    # ----------------------------
    # EMAIL MODE
    # ----------------------------
    if _is_email_question(user_question):
        try:
            emails = get_recent_emails_for_context(user_question)
            answer = ask_llm(user_question, emails)

            return jsonify({
                "answer": answer,
                "mode": "email_context",
                "evidence": {"emails": emails}
            })

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ----------------------------
    # FALLBACK MODE
    # ----------------------------
    try:
        answer = ask_llm(user_question, emails=[])

        return jsonify({
            "answer": answer,
            "mode": "fallback_llm",
            "confidence": "low",
            "evidence": {}
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ----------------------------
# HEALTH CHECK
# ----------------------------

@bp.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})