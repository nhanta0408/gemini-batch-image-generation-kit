"""The local dashboard: http://127.0.0.1:<port>. Nothing is hosted anywhere; the server only listens on this
machine, checks the Host header (no DNS rebinding) and requires a per-run token on every API call (no other
web page can press the buttons).

While it runs, a poller asks Gemini about unfinished jobs every POLL_SECONDS and downloads finished ones.
The Generate button starts `gbig.py gen submit` as a separate Python process, logged under .gbig/runs/.
"""

from __future__ import annotations

import json
import mimetypes
import os
import signal
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import batch as batch_mod
from . import gemini, manifest, memory, workspace
from .util import Stop, is_image, locked, now, read_json, write_json
from .workspace import ASPECT_RATIOS, IMAGE_PRICE, IMAGE_SIZES, TOOL_ROOT, Workspace

WEB = TOOL_ROOT / "web"
NO_PROJECT = "No project yet. Ask your AI assistant to create one (it runs ./gbig init <name>)."
POLL_SECONDS = 60


class App:
    def __init__(self, ws: Workspace, port: int):
        self.ws = ws
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.runs = {}
        self.poll_now = threading.Event()
        self.poller = {"last_at": "", "last_error": "", "running": False, "next_at": ""}

    # ---- helpers ----
    def current(self):
        """The machine's current project, re-read every request (the assistant may have run `./gbig use`
        or edited files), or None when no project exists yet."""
        try:
            self.ws = workspace.find(workspace.TOOL_ROOT)
        except Stop:
            ok = self.ws is not None and (self.ws.root / workspace.CONFIG_NAME).exists()
            self.ws = Workspace(self.ws.root) if ok else None
        return self.ws

    def fresh(self) -> Workspace:
        ws = self.current()
        if ws is None:
            raise Stop(NO_PROJECT)
        return ws

    def picture_rows(self, b, audit_rows: dict) -> dict:
        out = {}
        for rec in manifest.current(manifest.load(b)):
            a = audit_rows.get((rec["file"], rec["md5"]), {})
            st = a.get("status", "ok")
            url_path = a.get("found_at") if st == "renamed" else str((b.dir / rec["file"]).relative_to(self.ws.root))
            out.setdefault(rec["item"], []).append({
                "file": rec["file"], "variant": rec["variant"], "rev": rec["rev"], "job": rec["job"],
                "md5": rec["md5"], "generated_at": rec["generated_at"], "integrity": st,
                "found_at": a.get("found_at", ""),
                "url": f"/files/{url_path}?v={rec['md5'][:8]}" if st != "missing" else "",
            })
        for v in out.values():
            v.sort(key=lambda p: p["variant"])
        return out

    def overview(self) -> dict:
        ws = self.current()
        if ws is None:  # first run: the page explains what to ask the assistant instead of failing
            probe = Workspace(TOOL_ROOT)
            return {"workspace": None, "key_set": bool(probe.api_key()), "projects": [], "batches": [],
                    "projects_dir": str(workspace.projects_dir()), "aspect_ratios": list(ASPECT_RATIOS),
                    "jobs": [], "poller": self.poller, "runs": self.run_list(), "tool_root": str(TOOL_ROOT)}
        batches, all_jobs = [], []
        for b in ws.batches():
            s = memory.status(b)
            batches.append({"id": b.id, "topic": b.topic, "description": b.meta.get("description", ""),
                            "model": b.model, "status": s})
            for j in gemini.jobs(b):
                all_jobs.append(dict(j, batch=b.id))
        all_jobs.sort(key=lambda j: j.get("submitted_at", ""), reverse=True)
        projects = [{"name": n, "path": p, "current": Path(p).resolve() == ws.root}
                    for n, p in workspace.registered().items()]
        return {"workspace": {"name": ws.name, "root": str(ws.root)}, "key_set": bool(ws.api_key()),
                "projects": projects, "batches": batches, "jobs": all_jobs, "poller": self.poller,
                "projects_dir": str(workspace.projects_dir()), "aspect_ratios": list(ASPECT_RATIOS),
                "tool_root": str(TOOL_ROOT),
                "runs": self.run_list()}

    def create_project(self, body: dict) -> dict:
        name = str(body.get("name", "")).strip()
        folder = str(body.get("dir", "")).strip() or None
        self.ws = workspace.init(name, folder, str(body.get("title", "")).strip())
        return {"name": self.ws.name, "root": str(self.ws.root)}

    def choose_folder(self, body: dict) -> dict:
        """The browser cannot reveal a real folder path, so the server asks macOS to show its own picker."""
        if sys.platform != "darwin":
            raise Stop("The folder picker needs macOS. Type the folder path instead.")
        script = 'POSIX path of (choose folder with prompt "Where should this project keep its pictures?")'
        try:
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            raise Stop("No folder was chosen.")
        if out.returncode != 0:  # the person pressed Cancel
            return {"path": ""}
        return {"path": out.stdout.strip().rstrip("/") or "/"}

    def save_key(self, body: dict) -> dict:
        key = str(body.get("key", "")).strip()
        gemini.verify_key(Workspace(TOOL_ROOT), key)  # only a key Gemini accepts is saved
        workspace.set_key(key)
        return {"ok": True}

    def create_batch(self, body: dict) -> dict:
        ws = self.fresh()
        b = batch_mod.create(ws, str(body.get("id", "")).strip(), str(body.get("topic", "")).strip(),
                             str(body.get("description", "")).strip(), str(body.get("style", "")).strip(),
                             str(body.get("aspect_ratio", "")).strip())
        memory.write(ws)
        return {"id": b.id}

    def open_folder(self, batch_id: str, body: dict) -> dict:
        """Show the batch's pictures in Finder (Explorer / file manager elsewhere)."""
        b = self.fresh().batch(batch_id)
        target = b.output_dir if body.get("what") != "legacy" else b.output_dir / manifest.LEGACY
        target.mkdir(parents=True, exist_ok=True)
        opener = {"darwin": ["open"], "win32": ["explorer"]}.get(sys.platform, ["xdg-open"])
        subprocess.Popen(opener + [str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"path": str(target)}

    def switch_project(self, body: dict) -> dict:
        self.ws = workspace.use(str(body.get("project", "")))
        return {"name": self.ws.name, "root": str(self.ws.root)}

    def batch_view(self, batch_id: str) -> dict:
        ws = self.fresh()
        b = ws.batch(batch_id)
        audit = manifest.audit(ws)
        rows = {(r["file"], r["md5"]): r for r in audit["batches"].get(b.id, [])}
        pics = self.picture_rows(b, rows)
        flying = gemini.in_flight(b)
        items = []
        for it in b.items:
            d = it.to_dict()
            d.update(full_prompt=b.full_prompt(it), pictures=pics.get(it.id, []), in_flight=it.id in flying,
                     effective_aspect=b.aspect_ratio(it), variant_count=it.variant_count)
            items.append(d)
        return {"id": b.id, "meta": b.meta, "style": b.style, "model": b.model, "image_size": b.image_size,
                "default_aspect": b.meta.get("aspect_ratio") or ws.default("aspect_ratio", "1:1"),
                "items": items, "jobs": gemini.jobs(b), "status": memory.status(b), "errors": b.validate(),
                "choices": {"models": list(IMAGE_PRICE), "aspect_ratios": list(ASPECT_RATIOS),
                            "image_sizes": list(IMAGE_SIZES), "statuses": list(batch_mod.STATUSES)},
                "prices": IMAGE_PRICE, "key_set": bool(ws.api_key())}

    # ---- actions ----
    def update_item(self, batch_id: str, body: dict) -> dict:
        ws = self.fresh()
        with locked(ws.lock_path(batch_id)):
            b = ws.batch(batch_id)
            item = b.update_item(str(body.get("id", "")), body.get("fields") or {})
        memory.write(ws)
        return item.to_dict()

    def bulk_status(self, batch_id: str, body: dict) -> dict:
        ws = self.fresh()
        status = body.get("status")
        if status not in batch_mod.STATUSES:
            raise Stop("status must be draft, approved or drop")
        ids = set(body.get("ids") or [])
        with locked(ws.lock_path(batch_id)):
            b = ws.batch(batch_id)
            for it in b.items:
                if it.id in ids:
                    it.row["status"] = status
            b.save()
        memory.write(ws)
        return {"updated": len(ids)}

    def update_meta(self, batch_id: str, body: dict) -> dict:
        ws = self.fresh()
        allowed = {"topic", "description", "style", "model", "aspect_ratio", "image_size"}
        with locked(ws.lock_path(batch_id)):
            b = ws.batch(batch_id)
            for k, v in body.items():
                if k not in allowed:
                    raise Stop(f"'{k}' cannot be changed here")
                b.meta[k] = str(v or "").strip()
            errors = [e for e in b.validate() if e.startswith("batch.json")]
            if errors:
                raise Stop("; ".join(errors))
            b.save_meta()
        return b.meta

    def generate(self, batch_id: str, body: dict) -> dict:
        ws = self.fresh()
        b = ws.batch(batch_id)
        ids = [str(i) for i in (body.get("ids") or [])]
        if body.get("dry_run", True):
            items, notes = gemini.plan(b, ids or None)
            pictures = sum(i.variant_count for i in items)
            return {"items": [i.id for i in items], "pictures": pictures, "model": b.model, "notes": notes,
                    "estimated_usd": batch_mod.estimate_usd(b.model, pictures), "key_set": bool(ws.api_key()),
                    "errors": b.validate()}
        ws.require_key()
        cmd = [sys.executable, str(TOOL_ROOT / "gbig.py"), "gen", "submit", b.id, "--yes"]
        if ids:
            cmd += ["--ids", ",".join(ids)]
        return self.start_run("generate", b.id, cmd)

    def start_run(self, kind: str, batch_id: str, cmd: list) -> dict:
        runs_dir = self.ws.state_dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{batch_id}"
        log_path = runs_dir / f"{run_id}.log"
        fh = log_path.open("w", encoding="utf-8")
        fh.write("$ " + " ".join(cmd[1:]) + "\n")
        fh.flush()
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(self.ws.root),
                                env={**_env(), "GBIG_PROJECT": str(self.ws.root), "PYTHONUNBUFFERED": "1"})
        self.runs[run_id] = {"id": run_id, "kind": kind, "batch": batch_id, "started_at": now(),
                             "proc": proc, "log": log_path, "fh": fh}
        threading.Thread(target=self._after_run, args=(run_id,), daemon=True).start()
        return {"run": run_id}

    def _after_run(self, run_id: str) -> None:
        run = self.runs[run_id]
        run["proc"].wait()
        run["fh"].close()
        self.poll_now.set()  # a new job exists: check on it soon

    def run_list(self) -> list:
        out = []
        for r in sorted(self.runs.values(), key=lambda r: r["started_at"], reverse=True)[:10]:
            code = r["proc"].poll()
            try:
                tail = r["log"].read_text(encoding="utf-8")[-4000:]
            except OSError:
                tail = ""
            out.append({"id": r["id"], "kind": r["kind"], "batch": r["batch"], "started_at": r["started_at"],
                        "running": code is None, "exit_code": code, "log": tail})
        return out

    def cancel(self, batch_id: str, body: dict) -> dict:
        b = self.fresh().batch(batch_id)
        gemini.cancel(b, str(body.get("job", "")))
        self.poll_now.set()
        return {"ok": True}

    def audit(self) -> dict:
        ws = self.fresh()
        result = manifest.audit(ws)
        memory.write(ws, result)
        return result

    # ---- poller ----
    def poll_loop(self) -> None:
        while True:
            self.poll_once()
            self.poller["next_at"] = time.strftime("%H:%M:%S", time.localtime(time.time() + POLL_SECONDS))
            self.poll_now.wait(POLL_SECONDS)
            self.poll_now.clear()

    def poll_once(self) -> None:
        # Every project on this machine, not only the one on screen: switching projects must not stall a job.
        roots = {Path(p).resolve() for p in workspace.registered().values()} | ({self.ws.root} if self.ws else set())
        pending = [b for r in sorted(roots) if (r / workspace.CONFIG_NAME).exists()
                   for b in Workspace(r).batches() if any(not j.get("fetched") for j in gemini.jobs(b))]
        if not pending:
            return
        self.poller["running"] = True
        try:
            if not Workspace(TOOL_ROOT).api_key() and not (self.ws and self.ws.api_key()):
                self.poller["last_error"] = "GEMINI_API_KEY is missing"
                return
            for b in pending:
                gemini.refresh(b)
            self.poller["last_error"] = ""
        except Stop as e:
            self.poller["last_error"] = str(e)
        except Exception as e:  # keep polling; show the error in the dashboard
            self.poller["last_error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            self.poller["running"] = False
            self.poller["last_at"] = now()


def _env() -> dict:
    return dict(os.environ)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "gbig"

        def log_message(self, fmt, *args):  # quiet: the terminal stays readable for the user
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, code: int, body: bytes, ctype: str, extra: dict = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # Pictures carry ?v=<md5> so they may be cached; code must reload after a tool update.
            self.send_header("Cache-Control", "max-age=86400" if ctype.startswith("image/") else "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _api_guard(self) -> bool:
            if self.headers.get("X-GBIG-Token") != app.token:
                self._json(403, {"error": "Reload the dashboard page (the server was restarted)."})
                return False
            return True

        def _file(self, base: Path, rel: str, images_only: bool) -> None:
            target = (base / unquote(rel)).resolve()
            if base.resolve() not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            if images_only and not is_image(target):  # a project folder also holds .env, CSV, JSON
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    html = (WEB / "index.html").read_text(encoding="utf-8").replace("__GBIG_TOKEN__", app.token)
                    return self._send(200, html.encode(), "text/html; charset=utf-8",
                                      {"Content-Security-Policy": "default-src 'self'; img-src 'self' data:; "
                                                                  "style-src 'self'; script-src 'self'"})
                if path.startswith("/static/"):
                    return self._file(WEB, path[len("/static/"):], images_only=False)
                if path.startswith("/files/"):
                    ws = app.current()
                    if ws is None:
                        return self._send(404, b"not found", "text/plain")
                    return self._file(ws.root, path[len("/files/"):], images_only=True)
                if not path.startswith("/api/"):
                    return self._send(404, b"not found", "text/plain")
                parts = path.strip("/").split("/")
                if parts == ["api", "ping"]:  # no token: `gbig open` uses it to see whether we are running
                    return self._json(200, {"ok": True})
                if not self._api_guard():
                    return
                if parts == ["api", "overview"]:
                    return self._json(200, app.overview())
                if len(parts) == 3 and parts[1] == "batch":
                    return self._json(200, app.batch_view(parts[2]))
                if parts == ["api", "audit"]:
                    return self._json(200, app.audit())
                return self._json(404, {"error": "unknown endpoint"})
            except Stop as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            if not self._api_guard():
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                handler = {("api", "project"): app.switch_project, ("api", "projects"): app.create_project,
                           ("api", "choose-folder"): app.choose_folder, ("api", "key"): app.save_key,
                           ("api", "batches"): app.create_batch}.get(tuple(parts))
                if handler:
                    return self._json(200, handler(body))
                if parts == ["api", "refresh"]:
                    app.poll_now.set()
                    return self._json(200, {"ok": True})
                if len(parts) == 4 and parts[1] == "batch":
                    action = {"item": app.update_item, "bulk-status": app.bulk_status, "meta": app.update_meta,
                              "generate": app.generate, "cancel": app.cancel,
                              "open-folder": app.open_folder}.get(parts[3])
                    if action:
                        return self._json(200, action(parts[2], body))
                return self._json(404, {"error": "unknown endpoint"})
            except Stop as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


PID_FILE = workspace.STATE_DIR / "server.json"


def running(port: int) -> bool:
    """True when this tool's dashboard answers on the port (not just any program)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return r.headers.get("Server", "").startswith("gbig")
    except (urllib.error.URLError, OSError, ValueError):
        return False


def open_dashboard(port: int) -> str:
    """Start the dashboard in the background if needed, then open the browser. Safe to call any time."""
    url = f"http://127.0.0.1:{port}/"
    if not running(port):
        workspace.STATE_DIR.mkdir(parents=True, exist_ok=True)
        log = (workspace.STATE_DIR / "server.log").open("a", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "GBIG_PROJECT"}  # follow the current project
        subprocess.Popen([sys.executable, str(TOOL_ROOT / "gbig.py"), "serve", "--port", str(port), "--no-open"],
                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(TOOL_ROOT),
                         env=env, start_new_session=True)  # own session: survives closing the terminal
        for _ in range(40):
            time.sleep(0.25)
            if running(port):
                break
        else:
            raise Stop(f"The dashboard did not start. See {workspace.STATE_DIR / 'server.log'}")
    webbrowser.open(url)
    return url


def _stop_on_sigterm(*_):
    raise KeyboardInterrupt  # `gbig stop` sends SIGTERM: leave serve_forever the same way Ctrl+C does


def stop_dashboard() -> bool:
    info = read_json(PID_FILE, None)
    if not info:
        return False
    try:
        os.kill(int(info["pid"]), signal.SIGTERM)
    except (ProcessLookupError, ValueError, KeyError):
        pass
    PID_FILE.unlink()
    return True


def serve(ws, port: int, open_browser: bool) -> None:
    """ws may be None: the dashboard then shows a welcome page until a project exists."""
    app = App(ws, port)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    except OSError:
        if running(port):
            print(f"The dashboard is already running: http://127.0.0.1:{port}/")
            if open_browser:
                webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        raise Stop(f"Port {port} is used by another program. Try: ./gbig open --port {port + 1}")
    write_json(PID_FILE, {"pid": os.getpid(), "port": port, "started_at": now()})
    signal.signal(signal.SIGTERM, _stop_on_sigterm)
    threading.Thread(target=app.poll_loop, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    label = ws.name if ws else "(no project yet)"
    print(f"Dashboard for '{label}': {url}\nKeep this window open while you work. Ctrl+C stops it.", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Jobs already sent keep running at Gemini; start the dashboard again to fetch them.")
    finally:
        if (read_json(PID_FILE, {}) or {}).get("pid") == os.getpid():
            PID_FILE.unlink()
