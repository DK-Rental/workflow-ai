import asyncio
import os
import re
import uuid
import json
import sqlite3
import threading
import logging
from typing import Dict, List
from urllib.parse import quote

from flask import Flask, Response, request, jsonify, render_template
from werkzeug.utils import secure_filename
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.schema import Activity

from speech_service import get_transcript_from_file
from services.llm_client import generate_sop_from_transcript, refine_sop
from services.ai_search import ask_ai
from azure.storage.blob import BlobServiceClient
import config

app = Flask(__name__)

# ── Upload config ─────────────────────────────────────────────────────────────
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 * 1024
ALLOWED_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.wav', '.mp3', '.m4a'}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "loom_videos.db")


# ── DB ────────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id    TEXT PRIMARY KEY,
            title       TEXT,
            category    TEXT,
            tags        TEXT,
            blob_key    TEXT,
            status      TEXT,
            result_json TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    existing = [row[1] for row in conn.execute("PRAGMA table_info(videos)")]
    for col, definition in [
        ("category", "TEXT DEFAULT ''"),
        ("tags",     "TEXT DEFAULT '[]'"),
        ("blob_key", "TEXT DEFAULT ''"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {col} {definition}")
    conn.commit()
    conn.close()

init_db()

def recover_stale():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "UPDATE videos SET status='error', result_json=? WHERE status='processing'",
            (json.dumps({"error": "Server restarted while job was in progress"}),)
        )
        conn.commit()
    finally:
        conn.close()

recover_stale()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def save_to_db(video_id, title, status, result, category='', tags=None, blob_key=''):
    conn = sqlite3.connect(DB_PATH)
    try:
        if not title:
            row = conn.execute("SELECT title, category, tags, blob_key FROM videos WHERE video_id=?", (video_id,)).fetchone()
            if row:
                title    = row[0] or title
                category = category or row[1] or ''
                tags     = tags if tags is not None else json.loads(row[2] or '[]')
                blob_key = blob_key or row[3] or ''
        conn.execute(
            "INSERT OR REPLACE INTO videos (video_id, title, category, tags, blob_key, status, result_json) VALUES (?,?,?,?,?,?,?)",
            (video_id, title, category, json.dumps(tags or []), blob_key, status, json.dumps(result))
        )
        conn.commit()
    finally:
        conn.close()


# ── Blob ──────────────────────────────────────────────────────────────────────
def filename_to_blob_key(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0]
    name = re.sub(r'[^\w\s\-]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return f"{name}.json"


def upload_to_blob(blob_key: str, sop_data: dict) -> str:
    blob_service = BlobServiceClient.from_connection_string(config.AZURE_BLOB_CONNECTION_STRING)
    blob_client  = blob_service.get_blob_client(container=config.AZURE_BLOB_CONTAINER, blob=blob_key)
    blob_client.upload_blob(json.dumps(sop_data, indent=2), overwrite=True)
    return blob_key


# ── Background tasks ──────────────────────────────────────────────────────────
def process_video_task(video_id, file_path, original_filename, category, tags):
    try:
        print(f"[BG] Starting: {video_id} — {original_filename}")
        raw_text = get_transcript_from_file(file_path)
        sop_data = generate_sop_from_transcript(raw_text)
        blob_key = filename_to_blob_key(original_filename)
        upload_to_blob(blob_key, sop_data)
        save_to_db(video_id, original_filename, 'done', sop_data, category, tags, blob_key)
        print(f"[BG] Done: {video_id} → {blob_key}")
    except Exception as e:
        print(f"[BG] Error {video_id}: {e}")
        save_to_db(video_id, original_filename, 'error', {"error": str(e)}, category, tags)
    finally:
        if os.path.exists(file_path):
            try: os.remove(file_path)
            except Exception as ce: print(f"[BG] Cleanup warning: {ce}")


# ── Video uploader routes ─────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/video/upload', methods=['POST'])
def upload_video():
    if 'video' not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    files    = request.files.getlist('video')
    tags     = json.loads(request.form.get('tags', '[]'))
    category = request.form.get('category', '')

    valid_files = [f for f in files if f.filename != '']
    if not valid_files:
        return jsonify({"error": "No files selected"}), 400

    responses = []
    errors    = []

    for file in valid_files:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            errors.append({"filename": file.filename, "error": f"Unsupported type '{ext}'"})
            continue

        video_id  = str(uuid.uuid4())
        filename  = secure_filename(f"{video_id}_{file.filename}")
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(file_path)

        save_to_db(video_id, file.filename, 'processing', {}, category, tags)

        t = threading.Thread(
            target=process_video_task,
            args=(video_id, file_path, file.filename, category, tags),
            daemon=True,
        )
        t.start()

        responses.append({"video_id": video_id, "filename": file.filename, "status": "processing"})

    if responses and errors:
        return jsonify({"accepted": responses, "rejected": errors}), 207
    if errors:
        return jsonify({"error": "All files rejected", "details": errors}), 400
    return jsonify(responses), 202


@app.route('/api/video/status/<video_id>', methods=['GET'])
def video_status(video_id):
    conn = get_db()
    row  = conn.execute(
        "SELECT status, result_json, blob_key FROM videos WHERE video_id=?", (video_id,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Video not found"}), 404

    return jsonify({
        "status":   row["status"],
        "blob_key": row["blob_key"] or '',
        "result":   json.loads(row["result_json"]) if row["result_json"] else {},
    })


@app.route('/api/sop/refine', methods=['POST'])
def refine_sop_route():
    data        = request.get_json(silent=True) or {}
    video_id    = data.get('video_id', '')
    sop         = data.get('sop', {})
    instruction = data.get('instruction', '').strip()

    if not sop or not instruction:
        return jsonify({"error": "sop and instruction are required"}), 400

    try:
        refined = refine_sop(sop, instruction)

        if video_id:
            conn = get_db()
            row  = conn.execute("SELECT blob_key, title, category, tags FROM videos WHERE video_id=?", (video_id,)).fetchone()
            conn.close()
            if row and row["blob_key"]:
                try:
                    upload_to_blob(row["blob_key"], refined)
                except Exception as e:
                    print(f"Blob update warning: {e}")
            save_to_db(
                video_id,
                row["title"] if row else video_id,
                'done',
                refined,
                row["category"] if row else '',
                json.loads(row["tags"]) if row and row["tags"] else [],
                row["blob_key"] if row else '',
            )

        return jsonify({"sop": refined})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/video/cancel/<video_id>', methods=['POST'])
def cancel_video(video_id):
    conn = get_db()
    row = conn.execute("SELECT status FROM videos WHERE video_id=?", (video_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Video not found"}), 404
    if row["status"] != 'processing':
        return jsonify({"error": "Job is not in processing state"}), 400
    save_to_db(video_id, '', 'cancelled', {"error": "Cancelled by user"})
    return jsonify({"status": "cancelled"})


@app.route('/privacy')
def privacy():
    return "Privacy Policy: This is an internal AI tool for processing SOP videos. We do not share your data."

@app.route('/terms')
def terms():
    return "Terms of Service: For internal company use only."


# ── Joe bot ───────────────────────────────────────────────────────────────────
BOT_SETTINGS = BotFrameworkAdapterSettings(
    config.TEAMS_APP_ID,
    config.TEAMS_APP_PASSWORD,
    channel_auth_tenant=config.TENANT_ID
)
BOT_ADAPTER  = BotFrameworkAdapter(BOT_SETTINGS)

_history: Dict[str, List[Dict[str, str]]] = {}
_MAX_TURNS = 10

# ── Conversational detection ──────────────────────────────────────────────────
CONVERSATIONAL_PHRASES = [
    "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "bye", "goodbye", "see you", "take care",
    "how are you", "what can you do", "who are you", "help", "cheers",
    "appreciate it", "perfect", "great", "awesome", "got it", "ok", "okay",
    "sounds good", "cool", "nice", "no worries", "no problem",
]

def _is_conversational(question: str) -> bool:
    q = question.lower().strip()
    return any(q.startswith(phrase) for phrase in CONVERSATIONAL_PHRASES)


# ── AI call ───────────────────────────────────────────────────────────────────
def _call_ai(question: str, history: list) -> str:
    """Call vector RAG and format response with source titles."""
    result  = ask_ai(question, history)
    answer  = result.get("answer", "I'm having trouble accessing the SOPs right now.")
    sources = result.get("sources", [])
    logging.warning("[DEBUG SOURCES] %s", json.dumps([s.get("title", "") for s in sources]))

    if sources and not _is_conversational(question):
        seen   = []
        titles = []
        for s in sources:
            t = s.get("title", "")
            if t and t not in seen:
                seen.append(t)
                ext = os.path.splitext(t)[1].lower()
                if ext == ".json":
                    # Already a processed blob key, use as-is
                    blob_name = t
                else:
                    # Original filename (.docx, .xlsx, etc) — convert to blob key
                    blob_name = os.path.splitext(t)[0]
                    blob_name = re.sub(r'[^\w\s\-]', '', blob_name)
                    blob_name = re.sub(r'\s+', '_', blob_name.strip())
                    blob_name = f"{blob_name}.json"
                blob_url = f"https://dksopstorage123.blob.core.windows.net/sop/{quote(blob_name)}"
                logging.warning("[DEBUG URL] title=%s | blob_name=%s | url=%s", t, blob_name, blob_url)
                titles.append(f"- [{t}]({blob_url})")
        if titles:
            answer += "\n\n**Sources:**\n" + "\n".join(titles)

    return answer


async def on_turn(turn_context: TurnContext):
    logging.warning("[DEBUG TURN] type=%s | conv_type=%s | raw_text=%r | entities=%s",
        turn_context.activity.type,
        turn_context.activity.conversation.conversation_type,
        turn_context.activity.text,
        [e.type for e in (turn_context.activity.entities or [])]
    )
    if turn_context.activity.type != "message":
        return

    raw_text          = (turn_context.activity.text or "").strip()
    conversation_type = turn_context.activity.conversation.conversation_type
    conv_id           = turn_context.activity.conversation.id
    history           = _history.setdefault(conv_id, [])

    # ── Private chat ──────────────────────────────────────────────────────────
    if conversation_type == "personal":
        TurnContext.remove_recipient_mention(turn_context.activity)
        question = (turn_context.activity.text or "").strip()

        if not question:
            return

        logging.info("[PRIVATE CHAT] conv=%s q=%s", conv_id, question)
        answer = _call_ai(question, history)

        history.append({"role": "user",      "content": question})
        history.append({"role": "assistant", "content": answer})

        await turn_context.send_activity(answer)

    # ── Group chat ────────────────────────────────────────────────────────────
    else:
        has_native_mention = False
        if turn_context.activity.entities:
            for entity in turn_context.activity.entities:
                if entity.type == "mention":
                    mentioned = getattr(entity, "mentioned", None)
                    if mentioned and mentioned.id == turn_context.activity.recipient.id:
                        has_native_mention = True
                        break

        # Also detect <at>Joe</at> style mentions in raw text
        has_at_mention = bool(re.search(r'<at>[^<]*</at>', raw_text))
        has_hashtag    = raw_text.lower().startswith("#joe")

        if has_native_mention or has_at_mention or has_hashtag:
            TurnContext.remove_recipient_mention(turn_context.activity)
            question = (turn_context.activity.text or "").strip()
            # Strip any remaining <at>...</at> HTML tags
            question = re.sub(r'<at>[^<]*</at>', '', question).strip()

            if has_hashtag:
                question = question[4:].strip()

            if not question:
                return

            logging.info("[GROUP CHAT] Joe summoned! conv=%s q=%s", conv_id, question)
            answer = _call_ai(question, history)

            history.append({"role": "user",      "content": question})
            history.append({"role": "assistant", "content": answer})

            await turn_context.send_activity(answer)

        else:
            history.append({"role": "user", "content": f"Team context: {raw_text}"})

    if len(history) > _MAX_TURNS * 2:
        _history[conv_id] = history[-(_MAX_TURNS * 2):]


@app.route("/api/messages", methods=["POST"])
def messages():
    if "application/json" not in request.headers.get("Content-Type", ""):
        return Response(status=415)

    activity    = Activity().deserialize(request.json)
    auth_header = request.headers.get("Authorization", "")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            BOT_ADAPTER.process_activity(activity, auth_header, on_turn)
        )
    finally:
        loop.close()

    return Response(status=200)


# ── Security headers ──────────────────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers['Content-Security-Policy'] = "frame-ancestors 'self' teams.microsoft.com *.teams.microsoft.com *.skype.com;"
    if 'X-Frame-Options' in response.headers:
        del response.headers['X-Frame-Options']
    return response


if __name__ == "__main__":
    app.run(port=8000, debug=True)