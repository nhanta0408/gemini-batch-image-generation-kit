"""A stand-in for the Gemini Batch API, for tests and demos: no key, no cost.

    python3 tests/fake_gemini.py --port 8799      then set GEMINI_BASE_URL=http://127.0.0.1:8799 in .env

A job reports PENDING on the first status call and SUCCEEDED after that. A prompt containing FAIL gets no
image back, so partial failures can be tested. Pictures are small solid-colour PNGs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FILES, JOBS = {}, {}
LOCK = threading.Lock()


def png(seed: str, w: int = 64, h: int = 64) -> bytes:
    r, g, b = hashlib.md5(seed.encode()).digest()[:3]
    raw = b"".join(b"\x00" + bytes([r, g, b]) * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, data, headers=None):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        if not self.headers.get("x-goog-api-key"):
            return self._json(401, {"error": "no key"})
        path = self.path.split("?")[0]
        if path == "/v1beta/models":
            return self._json(200, {"models": [{"name": "models/fake"}]})
        if path.startswith("/v1beta/batches/"):
            name = path[len("/v1beta/"):]
            with LOCK:
                job = JOBS.get(name)
                if not job:
                    return self._json(404, {"error": "no such batch"})
                job["polls"] += 1
                state = job["state"] if job["state"] == "BATCH_STATE_CANCELLED" else (
                    "BATCH_STATE_PENDING" if job["polls"] == 1 else "BATCH_STATE_SUCCEEDED")
            info = {"name": name, "metadata": {"state": state}}
            if state == "BATCH_STATE_SUCCEEDED":
                info["response"] = {"responsesFile": f"files/result-{name.split('/')[-1]}"}
            return self._json(200, info)
        if path.startswith("/download/v1beta/files/result-"):
            job_id = path[len("/download/v1beta/files/result-"):].replace(":download", "")
            job = JOBS[f"batches/{job_id}"]
            lines = []
            for line in FILES[job["input"]].decode().splitlines():
                req = json.loads(line)
                text = req["request"]["contents"][0]["parts"][0]["text"]
                if "FAIL" in text:
                    lines.append(json.dumps({"key": req["key"], "error": {"code": 400, "message": "blocked"}}))
                    continue
                data = base64.b64encode(png(req["key"] + text)).decode()
                lines.append(json.dumps({"key": req["key"], "response": {"candidates": [{"content": {"parts": [
                    {"inlineData": {"mimeType": "image/png", "data": data}}]}}]}}))
            body = ("\n".join(lines) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self._json(404, {"error": "unknown"})

    def do_POST(self):
        if not self.headers.get("x-goog-api-key"):
            return self._json(401, {"error": "no key"})
        path = self.path.split("?")[0]
        body = self._body()
        host = self.headers.get("Host")
        with LOCK:
            if path == "/upload/v1beta/files":
                sid = f"s{len(FILES) + 1}"
                FILES[sid] = b""
                return self._json(200, {}, {"X-Goog-Upload-Url": f"http://{host}/upload-session/{sid}"})
            if path.startswith("/upload-session/"):
                sid = path.rsplit("/", 1)[-1]
                FILES[sid] = body
                return self._json(200, {"file": {"name": f"files/{sid}"}})
            if path.endswith(":batchGenerateContent"):
                req = json.loads(body)
                name = f"batches/fake{len(JOBS) + 1}"
                JOBS[name] = {"input": req["batch"]["input_config"]["file_name"].split("/")[-1], "polls": 0,
                              "state": "BATCH_STATE_PENDING"}
                return self._json(200, {"name": name})
            if path.endswith(":cancel"):
                name = path[len("/v1beta/"):-len(":cancel")]
                JOBS[name]["state"] = "BATCH_STATE_CANCELLED"
                return self._json(200, {})
        return self._json(404, {"error": "unknown"})


def start(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    a = ap.parse_args()
    print(f"Fake Gemini on http://127.0.0.1:{a.port}")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
