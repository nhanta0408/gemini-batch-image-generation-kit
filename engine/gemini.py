"""Gemini Batch API for images: half the interactive price, results within minutes to 24 hours.

Every job is written to batches/<id>/jobs/index.json the moment Gemini accepts it, so closing the terminal or
the dashboard loses nothing: the next `gen wait` (or the dashboard poller) picks up every job not yet fetched.
"""

from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import manifest, memory
from .batch import Batch, estimate_usd
from .util import MIME_EXT, Stop, locked, log, now, read_json, write_json

DONE = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}
KEY_SEP = "__v"


# ---- HTTP -------------------------------------------------------------------------------------------

def _request(method: str, url: str, key: str, body=None, raw: bytes = None, headers: dict = None,
             timeout: int = 60, to_file: Path = None):
    h = {"x-goog-api-key": key}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read()[:600].decode("utf-8", "replace")
        raise Stop(f"Gemini answered HTTP {e.code} for {method} {url.split('?')[0]}: {detail}")
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            raise Stop("SSL certificate error. On a python.org install, run 'Install Certificates.command' "
                       "in /Applications/Python 3.x, then try again.")
        raise Stop(f"Cannot reach Gemini ({e.reason}). Check the internet connection.")
    with resp:
        if to_file is not None:
            part = to_file.with_suffix(".part")
            with part.open("wb") as fh:
                for chunk in iter(lambda: resp.read(1 << 20), b""):
                    fh.write(chunk)
            part.replace(to_file)
            return resp.headers, None
        payload = resp.read()
        return resp.headers, (json.loads(payload) if payload else {})


def check_key(ws) -> str:
    verify_key(ws, ws.require_key())
    return "ok"


def verify_key(ws, key: str) -> None:
    if not key:
        raise Stop("Paste the API key first.")
    try:
        _request("GET", f"{ws.base_url()}/v1beta/models?pageSize=1", key)
    except Stop as e:
        if "HTTP 400" in str(e) or "HTTP 401" in str(e) or "HTTP 403" in str(e):
            raise Stop("Gemini did not accept this key. Copy it again from aistudio.google.com/apikey "
                       "and check that the Gemini API is enabled for it.")
        raise


def _upload_jsonl(ws, key: str, path: Path) -> str:
    size = path.stat().st_size
    headers, _ = _request(
        "POST", f"{ws.base_url()}/upload/v1beta/files", key, body={"file": {"display_name": path.name}},
        headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                 "X-Goog-Upload-Header-Content-Length": str(size),
                 "X-Goog-Upload-Header-Content-Type": "application/jsonl"})
    upload_url = headers.get("X-Goog-Upload-Url")
    if not upload_url:
        raise Stop("Gemini did not return an upload URL for the batch input file.")
    _, done = _request("POST", upload_url, key, raw=path.read_bytes(), timeout=600,
                       headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                                "X-Goog-Upload-Command": "upload, finalize"})
    return done["file"]["name"]


# ---- jobs index -------------------------------------------------------------------------------------

def _index_path(batch: Batch) -> Path:
    return batch.jobs_dir / "index.json"


def jobs(batch: Batch) -> list:
    return read_json(_index_path(batch), []) or []


def _set_job(batch: Batch, job_dir: str, **fields) -> None:
    entries = jobs(batch)
    for e in entries:
        if e["dir"] == job_dir:
            e.update(fields)
    write_json(_index_path(batch), entries)


def in_flight(batch: Batch) -> set:
    return {i for e in jobs(batch) if not e.get("fetched") for i in e["items"]}


# ---- submit -----------------------------------------------------------------------------------------

def plan(batch: Batch, ids=None) -> tuple:
    """(items to draw, notes). Without ids: approved items that have no picture yet or are marked redo.
    With ids: exactly those (each must be approved), drawn again even if they have a picture."""
    flying = in_flight(batch)
    have = manifest.current_by_item(manifest.load(batch))
    notes = []
    if ids:
        by_id = batch.by_id()
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise Stop(f"Unknown item id(s): {', '.join(unknown)}")
        not_ok = [i for i in ids if by_id[i].status != "approved"]
        if not_ok:
            raise Stop(f"Only approved items are drawn. Not approved: {', '.join(not_ok)}")
        chosen = [by_id[i] for i in ids if i not in flying]
        notes += [f"{i}: already being drawn, skipped" for i in ids if i in flying]
    else:
        chosen = [it for it in batch.items if it.status == "approved" and it.id not in flying
                  and (it.id not in have or it.result == "redo")]
    return chosen, notes


def submit(batch: Batch, ids=None, dry_run: bool = False) -> dict:
    errors = batch.validate()
    if errors:
        raise Stop("Fix prompts.csv first: " + "; ".join(errors[:5]))
    items, notes = plan(batch, ids)
    pictures = sum(it.variant_count for it in items)
    summary = {"items": [it.id for it in items], "pictures": pictures, "model": batch.model,
               "estimated_usd": estimate_usd(batch.model, pictures), "notes": notes, "dry_run": dry_run}
    log(f"Batch {batch.id} | model {batch.model} | {len(items)} items, {pictures} pictures | "
        f"estimated ${summary['estimated_usd']:.2f} (Batch API, -50%)")
    for it in items:
        log(f"  {it.id}  x{it.variant_count}  {it.aspect_ratio or batch.aspect_ratio(it)}  {it.summary[:70]}")
    for n in notes:
        log(f"  note: {n}")
    if dry_run or not items:
        if not items:
            log("Nothing to draw: approve items first, or mark a result as redo.")
        return summary
    key = batch.ws.require_key()
    stamp, n = f"job_{datetime.now():%Y%m%d-%H%M%S}", 1
    while True:  # two submits in the same second (double click, CLI + dashboard) must not share a folder
        name = stamp if n == 1 else f"{stamp}-{n}"
        job_dir = batch.jobs_dir / name
        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            n += 1
    input_path = job_dir / "input.jsonl"
    prompts = {}
    with input_path.open("w", encoding="utf-8") as fh:
        for it in items:
            prompts[it.id] = batch.full_prompt(it)
            image_config = {"aspectRatio": batch.aspect_ratio(it)}
            if batch.image_size:
                image_config["imageSize"] = batch.image_size
            request = {"contents": [{"parts": [{"text": prompts[it.id]}]}],
                       "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": image_config}}
            for v in range(1, it.variant_count + 1):
                fh.write(json.dumps({"key": f"{it.id}{KEY_SEP}{v}", "request": request}, ensure_ascii=False) + "\n")
    log("Uploading the request file to Gemini...")
    file_name = _upload_jsonl(batch.ws, key, input_path)
    _, created = _request(
        "POST", f"{batch.ws.base_url()}/v1beta/models/{batch.model}:batchGenerateContent", key,
        body={"batch": {"display_name": f"{batch.id}-{name}", "input_config": {"file_name": file_name}}})
    batch_name = created["name"]
    write_json(job_dir / "job.json", {"batch_name": batch_name, "model": batch.model, "submitted_at": now(),
                                      "input_file": file_name, "prompts": prompts})
    with locked(batch.lock):
        entries = jobs(batch)
        entries.append({"dir": name, "batch_name": batch_name, "model": batch.model,
                        "items": {it.id: it.variant_count for it in items}, "count": pictures,
                        "state": "JOB_STATE_PENDING", "submitted_at": now(), "fetched": False,
                        "estimated_usd": summary["estimated_usd"]})
        write_json(_index_path(batch), entries)
    memory.write(batch.ws)
    summary["job"] = name
    log(f"Submitted {batch_name} as {name}. Gemini usually finishes within minutes, at most 24 hours.")
    return summary


# ---- poll + fetch -----------------------------------------------------------------------------------

def _state(info: dict) -> str:
    s = (info.get("metadata") or {}).get("state") or info.get("state") or "UNKNOWN"
    return s.replace("BATCH_STATE_", "JOB_STATE_")


def _result_file(info: dict):
    for c in (info.get("response") or {}, (info.get("metadata") or {}).get("output") or {},
              info.get("dest") or {}, info.get("output") or {}):
        name = c.get("responsesFile") or c.get("fileName")
        if name:
            return name
    return None


def _find_image(node):
    if isinstance(node, dict):
        inline = node.get("inlineData") or node.get("inline_data")
        if isinstance(inline, dict) and inline.get("data"):
            return inline.get("mimeType") or inline.get("mime_type") or "image/png", inline["data"]
        for v in node.values():
            found = _find_image(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_image(v)
            if found:
                return found
    return None


def _fetch(batch: Batch, key: str, entry: dict, info: dict) -> None:
    job_dir = batch.jobs_dir / entry["dir"]
    job = read_json(job_dir / "job.json", {}) or {}
    results = job_dir / "results.jsonl"
    if not results.exists():
        file_name = _result_file(info)
        if not file_name:
            raise Stop(f"{entry['dir']}: succeeded but no result file in Gemini's answer")
        _request("GET", f"{batch.ws.base_url()}/download/v1beta/{file_name}:download?alt=media", key,
                 timeout=900, to_file=results)
    got, failed = {}, []
    with results.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            item_id, _, v = (row.get("key") or "").rpartition(KEY_SEP)
            found = _find_image(row.get("response") or {})
            if not found or item_id not in entry["items"] or not v.isdigit():
                failed.append(row.get("key") or "?")
                continue
            got.setdefault(item_id, []).append((int(v), found))
    job_info = {"dir": entry["dir"], "batch_name": entry["batch_name"], "model": entry["model"],
                "prompts": job.get("prompts", {})}
    with locked(batch.lock):
        fresh = Batch(batch.ws, batch.dir)
        man = manifest.load(fresh)
        by_id = fresh.by_id()
        for item_id, variants in got.items():
            manifest.retire_item(fresh, man, item_id)
            for v, (mime, data) in sorted(variants):
                manifest.add(fresh, man, item_id, v, base64.b64decode(data), MIME_EXT.get(mime, ".png"), job_info)
            if item_id in by_id:
                by_id[item_id].row["result"] = ""  # a new picture needs a new review
        manifest.save(fresh, man)
        fresh.save()
        ok = sum(len(v) for v in got.values())
        _set_job(fresh, entry["dir"], fetched=True, ok=ok, failed=failed, fetched_at=now())
    log(f"  {entry['dir']}: {ok}/{entry['count']} pictures saved to batches/{batch.id}/images/")
    if failed:
        log(f"  failed: {', '.join(failed)} (Generate again to retry them)")


def refresh(batch: Batch) -> list:
    """Ask Gemini once about every job not fetched yet; download finished ones. Returns the updated jobs."""
    pending = [e for e in jobs(batch) if not e.get("fetched")]
    if not pending:
        return jobs(batch)
    key = batch.ws.require_key()
    for e in pending:
        try:
            _, info = _request("GET", f"{batch.ws.base_url()}/v1beta/{e['batch_name']}", key)
        except Stop as err:
            with locked(batch.lock):
                _set_job(batch, e["dir"], last_error=str(err), checked_at=now())
            log(f"  {e['dir']}: {err}")
            continue
        state = _state(info)
        with locked(batch.lock):
            _set_job(batch, e["dir"], state=state, checked_at=now(), last_error="")
        log(f"  {batch.id}/{e['dir']}: {e['count']} pictures, {state}")
        if state == "JOB_STATE_SUCCEEDED":
            _fetch(batch, key, e, info)
        elif state in DONE:
            with locked(batch.lock):
                _set_job(batch, e["dir"], fetched=True, ok=0, failed=list(e["items"]), fetched_at=now())
    memory.write(batch.ws)
    return jobs(batch)


def cancel(batch: Batch, job_dir: str) -> None:
    entry = next((e for e in jobs(batch) if e["dir"] == job_dir), None)
    if entry is None:
        raise Stop(f"No job {job_dir} in batch {batch.id}")
    if entry.get("fetched"):
        raise Stop(f"{job_dir} is already finished")
    _request("POST", f"{batch.ws.base_url()}/v1beta/{entry['batch_name']}:cancel", batch.ws.require_key(), body={})
    log(f"Asked Gemini to cancel {job_dir}. The next refresh records the final state.")
