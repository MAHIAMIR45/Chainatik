#!/usr/bin/env python3
"""
Savetik Douyin Downloader — Flask Web Server
Deploy on Render as Web Service.
"""

import os
import logging
from flask import Flask, request, jsonify, render_template_string, send_file

from savetik import download_video

# ── Configuration ──────────────────────────────────────────────────────────

app = Flask(__name__)
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/tmp/savetik_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("savetik_web")

# ── HTML Interface ─────────────────────────────────────────────────────────

INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Savetik Douyin Downloader</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 680px; margin: 50px auto; padding: 20px;
            background: #0f172a; color: #e2e8f0;
        }
        h1 { font-size: 1.8rem; margin-bottom: 0.5rem; }
        p { color: #94a3b8; margin-bottom: 2rem; }
        .card {
            background: #1e293b; border-radius: 12px; padding: 24px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        }
        label { display: block; margin-bottom: 8px; font-weight: 600; }
        input {
            width: 100%; padding: 12px 16px; font-size: 1rem;
            border: 1px solid #334155; border-radius: 8px;
            background: #0f172a; color: #e2e8f0;
            margin-bottom: 16px;
        }
        input:focus { outline: none; border-color: #3b82f6; }
        button {
            width: 100%; padding: 12px; font-size: 1rem; font-weight: 600;
            background: #3b82f6; color: white; border: none; border-radius: 8px;
            cursor: pointer; transition: background 0.2s;
        }
        button:hover { background: #2563eb; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        #status {
            margin-top: 16px; padding: 12px; border-radius: 8px;
            display: none; font-size: 0.95rem;
        }
        #status.loading { display: block; background: #1e3a5f; color: #93c5fd; }
        #status.success { display: block; background: #14532d; color: #86efac; }
        #status.error { display: block; background: #450a0a; color: #fca5a5; }
        #status a { color: #93c5fd; text-decoration: underline; }
        .footer { margin-top: 24px; font-size: 0.85rem; color: #64748b; text-align: center; }
    </style>
</head>
<body>
    <h1>🎥 Savetik Douyin Downloader</h1>
    <p>Download Douyin &amp; TikTok videos without watermark. Paste the video URL below.</p>

    <div class="card">
        <label for="url">Video URL</label>
        <input type="url" id="url" placeholder="https://v.douyin.com/xxxxxx/ or https://www.tiktok.com/@user/video/xxxx" />
        <button id="btn" onclick="startDownload()">Download Video</button>
        <div id="status"></div>
    </div>

    <div class="footer">
        Powered by <a href="https://savetik.co" target="_blank" style="color:#64748b;">Savetik.co</a>
        &middot; Deployed on Render
    </div>

    <script>
        async function startDownload() {
            const url = document.getElementById('url').value.trim();
            const btn = document.getElementById('btn');
            const status = document.getElementById('status');

            if (!url) {
                status.className = 'error';
                status.textContent = 'Please enter a video URL';
                return;
            }

            btn.disabled = true;
            status.className = 'loading';
            status.textContent = '⏳ Processing... This may take 30-60 seconds.';

            try {
                const response = await fetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url })
                });
                const data = await response.json();

                if (data.success) {
                    const sizeMB = (data.size / (1024 * 1024)).toFixed(1);
                    status.className = 'success';
                    status.innerHTML = `
                        ✅ Download ready!<br>
                        <strong>${data.filename}</strong> (${sizeMB} MB)<br><br>
                        <a href="/file/${data.filename}" target="_blank">📥 Click here to save video</a>
                    `;
                } else {
                    status.className = 'error';
                    status.textContent = '❌ Error: ' + (data.error || 'Unknown error');
                }
            } catch (e) {
                status.className = 'error';
                status.textContent = '❌ Network error: ' + e.message;
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
    """JSON API endpoint for downloading Douyin videos."""
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"success": False, "error": "Missing 'url' parameter"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"success": False, "error": "Invalid URL format"}), 400

    log.info("API request: %s", url)
    result = download_video(url, output_dir=DOWNLOAD_DIR)

    if result["success"]:
        return jsonify({
            "success": True,
            "filename": result["filename"],
            "size": result["size"],
            "download_url": f"/file/{result['filename']}",
        })
    else:
        return jsonify({
            "success": False,
            "error": result.get("error", "Unknown error"),
        }), 500


@app.route("/file/<filename>")
def serve_file(filename):
    """Serve the downloaded video file for download."""
    safe_name = os.path.basename(filename)  # Prevent path traversal
    file_path = os.path.join(DOWNLOAD_DIR, safe_name)

    if not os.path.exists(file_path):
        return jsonify({"error": "File not found or expired"}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=safe_name,
        mimetype="video/mp4",
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "savetik-downloader"})


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
