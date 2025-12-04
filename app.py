import io
import os
import base64
from urllib.parse import urlparse
from flask import Flask, request, jsonify, abort
import requests
from openai import OpenAI

app = Flask(__name__)

RELAY_TOKEN = os.getenv("RELAY_TOKEN")  # set this in Render
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # set this in Render
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "")  # e.g. "dropboxusercontent.com,githubusercontent.com,googleusercontent.com"

def require_auth():
    auth = request.headers.get("Authorization", "")
    if not RELAY_TOKEN:
        return
    if not auth.startswith("Bearer "):
        abort(401)
    token = auth.split(" ", 1)[1].strip()
    if token != RELAY_TOKEN:
        abort(403)

def host_allowed(u: str) -> bool:
    if not ALLOWED_HOSTS:
        return True
    host = urlparse(u).hostname or ""
    return any(h.strip() and h.strip() in host for h in ALLOWED_HOSTS.split(","))

def fetch_bytes(url: str):
    if not host_allowed(url):
        abort(400, f"URL host not allowed by ALLOWED_HOSTS: {url}")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.content

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/fetch")
def fetch():
    """Fetch a remote file and return it (base64), plus a text preview."""
    require_auth()
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")
    if not url:
        abort(400, "Missing 'url'")
    raw = fetch_bytes(url)
    path = urlparse(url).path
    filename = os.path.basename(path) or "file.dat"
    text_preview = None
    try:
        txt = raw.decode("utf-8", errors="replace")
        text_preview = txt[:4000]
    except Exception:
        pass
    return jsonify({
        "filename": filename,
        "size_bytes": len(raw),
        "base64": base64.b64encode(raw).decode("ascii"),
        "text_preview": text_preview,
    })

@app.post("/upload-to-openai")
def upload_to_openai():
    """
    Fetch a remote file and upload it to OpenAI Files.
    Returns file_id. Optionally starts a run if assistant_id/thread_id provided.
    """
    require_auth()
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")
    purpose = data.get("purpose", "assistants")
    assistant_id = data.get("assistant_id")
    thread_id = data.get("thread_id")
    explicit_filename = data.get("filename")

    if not url:
        abort(400, "Missing 'url'")

    if not OPENAI_API_KEY:
        abort(500, "OPENAI_API_KEY not set on server")

    raw = fetch_bytes(url)
    path = urlparse(url).path
    guessed_name = os.path.basename(path) or "remote.dat"
    filename = explicit_filename or guessed_name

    client = OpenAI(api_key=OPENAI_API_KEY)

    file_obj = client.files.create(
        file=(filename, io.BytesIO(raw), "application/octet-stream"),
        purpose=purpose
    )

    result = {"file_id": file_obj.id, "filename": filename}

    if assistant_id and thread_id:
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=f"New data uploaded: {filename}",
            attachments=[{"file_id": file_obj.id, "tools": [{"type": "file_search"}]}]
        )
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=assistant_id
        )
        result["run_id"] = run.id

    return jsonify(result)
