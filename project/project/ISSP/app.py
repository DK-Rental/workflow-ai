import os
import re
import uuid
import json
import sqlite3
import threading
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
from speech_service import get_transcript_from_file
from services.llm_client import generate_sop_from_transcript, refine_sop
from azure.storage.blob import BlobServiceClient
import config

app = Flask(__name__)

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
        # If title is empty, preserve the existing one
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
    name = os.path.splitext(original_filename)[0]   # strip .mp4 / .mov etc
    name = re.sub(r'[^\w\s\-]', '', name)            # remove special chars
    name = re.sub(r'\s+', '_', name.strip())         # spaces → underscores
    return f"{name}.json"                             # e.g. Tenant.json


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


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/video/upload', methods=['POST'])
def upload_video():
    if 'video' not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    files     = request.files.getlist('video')
    tags      = json.loads(request.form.get('tags', '[]'))
    category  = request.form.get('category', '')

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
    """Take an existing SOP and an instruction, and return a refined version."""
    data        = request.get_json(silent=True) or {}
    video_id    = data.get('video_id', '')
    sop         = data.get('sop', {})
    instruction = data.get('instruction', '').strip()

    if not sop or not instruction:
        return jsonify({"error": "sop and instruction are required"}), 400

    try:
        refined = refine_sop(sop, instruction)

        # Update blob and DB with refined SOP if we have a video_id
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
    """Mark a processing job as cancelled. The background thread will finish
    naturally but the result will be discarded since status is already cancelled."""
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

@app.after_request
def add_security_headers(response):
    response.headers['Content-Security-Policy'] = "frame-ancestors 'self' teams.microsoft.com *.teams.microsoft.com *.skype.com;"
    if 'X-Frame-Options' in response.headers:
        del response.headers['X-Frame-Options']
    return response


if __name__ == "__main__":
    app.run(port=3978, debug=True)