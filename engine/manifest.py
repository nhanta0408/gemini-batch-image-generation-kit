"""manifest.json: which job produced which file, and the file's MD5 at the moment the tool saved it.

The MD5 recorded at generation time never changes. `audit` compares it with what is on disk now:

    ok         file is where the tool put it, byte-identical to the Gemini output
    modified   file is there but its content changed (edited, re-saved, compressed...)
    renamed    file is gone from its path but the same bytes exist elsewhere in the workspace
    missing    file is gone and no copy with the same MD5 exists in the workspace
    untracked  an image in a batch's images/ folder that no job produced (added by hand)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .util import is_image, md5_file, now, read_json, write_json

STATUS_ORDER = ("ok", "modified", "renamed", "missing", "untracked")
LEGACY = "legacy"


def load(batch) -> dict:
    return read_json(batch.manifest_path, {"files": []}) or {"files": []}


def save(batch, manifest: dict) -> None:
    write_json(batch.manifest_path, manifest)


def current(manifest: dict) -> list:
    return [r for r in manifest["files"] if r.get("current")]


def current_by_item(manifest: dict) -> dict:
    out = {}
    for r in current(manifest):
        out.setdefault(r["item"], []).append(r)
    for recs in out.values():
        recs.sort(key=lambda r: r["variant"])
    return out


def file_name(item_id: str, variant: int, ext: str) -> str:
    return f"{item_id}{'' if variant == 1 else f'-{variant}'}{ext}"


def retire_item(batch, manifest: dict, item_id: str) -> None:
    """Before an item is drawn again: move its current pictures to images/legacy/ so nothing is overwritten
    and the person can still find every earlier version."""
    old_dir = batch.output_dir / LEGACY
    for rec in [r for r in current(manifest) if r["item"] == item_id]:
        rec["current"] = False
        rec["retired_at"] = now()
        path = batch.dir / rec["file"]
        if path.exists():
            old_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(file_name(item_id, rec["variant"], path.suffix)).stem
            target = _free(old_dir / f"{stem}-r{rec['rev']}{path.suffix}")
            shutil.move(str(path), str(target))
            rec["file"] = str(target.relative_to(batch.dir))


def add(batch, manifest: dict, item_id: str, variant: int, data: bytes, ext: str, job: dict) -> dict:
    batch.output_dir.mkdir(parents=True, exist_ok=True)
    target = batch.output_dir / file_name(item_id, variant, ext)
    if target.exists():  # a hand-made file sits where the new picture goes: keep it, never overwrite
        old_dir = batch.output_dir / LEGACY
        old_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(_free(old_dir / f"{target.stem}-replaced{target.suffix}")))
    target.write_bytes(data)
    revs = [r["rev"] for r in manifest["files"] if r["item"] == item_id and r["variant"] == variant]
    rec = {
        "file": str(target.relative_to(batch.dir)),
        "item": item_id,
        "variant": variant,
        "rev": (max(revs) + 1) if revs else 1,
        "current": True,
        "job": job["dir"],
        "gemini_batch": job["batch_name"],
        "model": job["model"],
        "prompt": job["prompts"].get(item_id, ""),
        "generated_at": now(),
        "md5": md5_file(target),
        "bytes": len(data),
    }
    manifest["files"].append(rec)
    return rec


def _free(path: Path) -> Path:
    n, candidate = 2, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate


# ---- audit ------------------------------------------------------------------------------------------

def _scan(ws) -> dict:
    """relative path -> md5 for every image in the workspace, with a size/mtime cache so rescans are fast."""
    cache_path = ws.state_dir / "hash-cache.json"
    cache = read_json(cache_path, {}) or {}
    fresh = {}
    for dirpath, dirnames, filenames in os.walk(ws.root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "jobs"]
        for name in filenames:
            p = Path(dirpath) / name
            if not is_image(p):
                continue
            rel = str(p.relative_to(ws.root))
            st = p.stat()
            hit = cache.get(rel)
            if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
                fresh[rel] = hit
            else:
                fresh[rel] = [st.st_size, st.st_mtime_ns, md5_file(p)]
    write_json(cache_path, fresh)
    return {rel: v[2] for rel, v in fresh.items()}


def audit(ws) -> dict:
    """{"batches": {id: [rows]}, "untracked": [rows], "counts": {status: n}} where a row is a manifest record
    plus status, path (workspace-relative, where the tool put it) and found_at (where it is now)."""
    on_disk = _scan(ws)
    by_md5 = {}
    for rel, md5 in on_disk.items():
        by_md5.setdefault(md5, []).append(rel)
    batches, claimed = {}, set()
    recorded_paths = set()
    all_rows = []
    for b in ws.batches():
        rows = []
        for rec in load(b)["files"]:
            rel = str((b.dir / rec["file"]).relative_to(ws.root))
            recorded_paths.add(rel)
            rows.append(dict(rec, path=rel, batch=b.id))
        batches[b.id] = rows
        all_rows.extend(rows)
    for row in all_rows:
        rel = row["path"]
        if rel in on_disk:
            row["status"] = "ok" if on_disk[rel] == row["md5"] else "modified"
            row["found_at"] = rel
            claimed.add(rel)
    for row in all_rows:
        if "status" in row:
            continue
        moved = [p for p in by_md5.get(row["md5"], []) if p not in recorded_paths and p not in claimed]
        home = str(Path(row["path"]).parent)
        batch_root = str(Path("batches") / row["batch"])
        # several copies: the one next to the original is the rename; others are copies
        moved.sort(key=lambda p: (not p.startswith(home + "/"), not p.startswith(batch_root + "/"), p))
        if moved:
            row["status"], row["found_at"] = "renamed", moved[0]
            claimed.add(moved[0])
        else:
            row["status"], row["found_at"] = "missing", ""
    untracked = []
    for rel in sorted(on_disk):
        parts = Path(rel).parts
        if len(parts) >= 4 and parts[0] == "batches" and parts[2] == "images" and rel not in claimed:
            untracked.append({"batch": parts[1], "path": rel, "found_at": rel, "md5": on_disk[rel],
                              "status": "untracked", "current": True})
    counts = {s: 0 for s in STATUS_ORDER}
    for row in all_rows + untracked:
        if row.get("current"):
            counts[row["status"]] += 1
    return {"batches": batches, "untracked": untracked, "counts": counts, "checked_at": now()}
