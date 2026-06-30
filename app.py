#!/usr/bin/env python3
"""
Render Web Service — Savetik Douyin Downloader
Provides a simple JSON API and a minimal web form.
"""

import os
import uuid
import shutil
from flask import Flask, request, jsonify, render_template_string, send_file

from savetik import download_video

app = Flask(__name__)

# Render provides /tmp as writable storage (ephemeral — files disappear on restart)
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/tmp/savetik_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── Simple HTML form (optional) ────────────────────────────────────────────

INDEX_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Savetik Douyin Downloader</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: system-ui, sans-serif; max-width: 640px; margin: 3rem auto; padding: 1rem; }
        input, button { font-size: 1rem; padding: 0.6rem 1rem; width: 100%; box-sizing: border-box; }
        input { margin-bottom: 0.5rem; }
        button { background: #2563eb; color: #fff; border: none; cursor: pointer; }
        button:disabled { opacity: 0.6; }
        #result { margin-top: 1rem; white-space: pre-wrap; }
        .error { color: #dc2626; }
        .ok { color: #16a34a; }
    </style>
</head>
<body>
    <h2>🎥 Savetik Douyin Downloader</h2>
    <p>Paste a Douyin / TikTok URL below to download the no-watermark video.</p>
    <input type="url" id="url" placeholder="https://v.douyin.com/xxxxxx/" />
    <button id="btn" onclick="download()">Download</button>
    <div id="result"></div>
    <script>
        async function download() {
            const url = document.getElementById('url').value.trim();
            const btn = document.getElementById('btn');
            const res = document.getElementById('result');
            if (!url) { res.className = 'error'; res.textContent = 'Please enter a URL'; return; }
            btn.disabled = true;
            res.className = '';
            res.textContent = 'Processing… this may take 30-60 seconds.';
            try {
                const r = await fetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url })
                });
                const data = await r.json();
                if (data.success) {
                    res.className = 'ok';
                    res.innerHTML = `✅ Download ready!<br>
                        <a href="/file/${data.filename}" target="_blank">Click to save video</a><br>
                        <small>${(data.size / 1024 / 1024).toFixed(1)} MB</small>`;
                } else {
                    res.className = 'error';
                    res.textContent = '❌ Error: ' + (data.error || 'Unknown error');
                }
            } catch (e) {
                res.className = 'error';
                res.textContent = '❌ Network error: ' + e.message;
            }
            btn.disabled = false;
        }
    </script>
</body>
</html>
"""

# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "Missing 'url' parameter"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"success": False, "error": "Invalid URL"}), 400

    result = download_video(url, output_dir=DOWNLOAD_DIR)
    if result["success"]:
        return jsonify({
            "success": True,
            "filename": result["filename"],
            "size": result["size"],
            "download_url": f"/file/{result['filename']}",
        })
    else:
        return jsonify({"success": False, "error": result.get("error", "Unknown error")}), 500


@app.route("/file/<filename>")
def serve_file(filename):
    """Serve the downloaded video file."""
    safe = os.path.basename(filename)  # prevent path traversal
    path = os.path.join(DOWNLOAD_DIR, safe)
    if not os.path.exists(path):
        return "File not found", 404
    return send_file(path, as_attachment=True, download_name=safe)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
