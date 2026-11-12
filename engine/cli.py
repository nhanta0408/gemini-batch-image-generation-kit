"""./gbig <command>, run from the tool folder. Acts on the current project (see engine/workspace.py)."""

from __future__ import annotations

import argparse
import csv
import getpass
import io
import json
import sys
from pathlib import Path

from . import batch as batch_mod
from . import gemini, manifest, memory, workspace
from .util import Stop, locked


def cmd_init(a):
    ws = workspace.init(a.name, a.dir or None, a.title)
    print(f"Project '{a.name}' created at {ws.root} and selected.")
    if not ws.api_key():
        print("Next: ./gbig key set   (paste the Gemini API key)")
    print("Then: ./gbig new <batch-id> --topic \"...\"   and   ./gbig serve")


def cmd_use(a):
    ws = workspace.use(a.project)
    print(f"Current project: {ws.name} ({ws.root})")


def cmd_projects(a):
    known = workspace.registered()
    current = workspace.local().get("current")
    print(f"Default folder for new projects: {workspace.projects_dir()}")
    for name, path in known.items():
        print(f"  {'*' if name == current else ' '} {name:<30} {path}")
    if not known:
        print("  (none yet: ./gbig init <name>)")


def cmd_config(a):
    if a.projects_dir:
        print(f"New projects go to {workspace.set_projects_dir(Path(a.projects_dir))}")
    else:
        print(f"projects_dir: {workspace.projects_dir()}")


def cmd_key(a):
    if a.action == "set":
        key = sys.stdin.readline().strip() if not sys.stdin.isatty() else getpass.getpass("Gemini API key: ")
        workspace.set_key(key)
        print("Saved to .env in the tool folder (only readable by you, never committed).")
    print("Checking the key with Gemini...", end=" ", flush=True)
    print(gemini.check_key(_key_context()))


def _key_context():
    """A key check needs no project; fall back to a throwaway context when none exists yet."""
    try:
        return workspace.find()
    except Stop:
        return workspace.Workspace(workspace.TOOL_ROOT)


def cmd_new(a):
    ws = workspace.find()
    b = batch_mod.create(ws, a.batch, a.topic, a.description, a.style, a.aspect, a.model)
    memory.write(ws)
    print(f"Batch created: batches/{b.id}/  (fill prompts.csv, then ./gbig validate {b.id})")


def cmd_import(a):
    """Append rows from a CSV or JSON list (what the agent wrote) to prompts.csv; ids must be new."""
    ws = workspace.find()
    src = Path(a.file)
    text = src.read_text(encoding="utf-8-sig")
    rows = json.loads(text) if src.suffix == ".json" else list(csv.DictReader(io.StringIO(text)))
    with locked(ws.lock_path(a.batch)):
        b = ws.batch(a.batch)
        existing = set(b.by_id())
        clash = [r.get("id") for r in rows if r.get("id") in existing]
        if clash and not a.replace:
            raise Stop(f"These ids already exist: {', '.join(clash[:10])} (use --replace to overwrite them)")
        by_id = b.by_id()
        for n, r in enumerate(rows, start=1):
            r = {k: str(v) if not isinstance(v, list) else "|".join(map(str, v)) for k, v in r.items()}
            r.setdefault("status", "draft")
            if r.get("id") in by_id:
                by_id[r["id"]].row.update({k: v for k, v in r.items() if k in batch_mod.COLUMNS})
            else:
                b.items.append(batch_mod.Item(r, n))
        errors = b.validate()
        if errors:
            raise Stop("Not imported: " + "; ".join(errors[:10]))
        if a.dry_run:
            print(f"DRY RUN: {len(rows)} rows are valid; nothing written.")
            return
        b.save()
    memory.write(ws)
    print(f"Imported {len(rows)} rows into batches/{a.batch}/prompts.csv")


def cmd_validate(a):
    b = workspace.find().batch(a.batch)
    errors = b.validate()
    for e in errors:
        print(f"  {e}")
    print(f"{len(b.items)} items, {len(errors)} problem(s)")
    if errors:
        sys.exit(1)


def cmd_prompts(a):
    b = workspace.find().batch(a.batch)
    ids = set(a.ids.split(",")) if a.ids else None
    for it in b.items:
        if ids and it.id not in ids:
            continue
        print(f"=== {it.id} [{it.status or 'draft'}] x{it.variant_count} {b.aspect_ratio(it)} | {it.topic}")
        print(b.full_prompt(it) + "\n")


def cmd_gen(a):
    ws = workspace.find()
    if a.action == "submit":
        b = ws.batch(a.batch)
        ids = [i for i in (a.ids or "").split(",") if i] or None
        dry = a.dry_run or not a.yes
        gemini.submit(b, ids, dry_run=dry)
        if dry and not a.dry_run:
            print("\nNot sent. Add --yes to send (or press Generate in the dashboard).")
    elif a.action == "wait":
        import time
        while True:
            batches = [ws.batch(a.batch)] if a.batch else ws.batches()
            pending = 0
            for b in batches:
                pending += sum(1 for j in gemini.refresh(b) if not j.get("fetched"))
            if not pending:
                print("Every job is fetched.")
                return
            if a.once:
                print(f"{pending} job(s) still running.")
                return
            time.sleep(60)
    elif a.action == "cancel":
        gemini.cancel(ws.batch(a.batch), a.job)


def cmd_audit(a):
    ws = workspace.find()
    result = manifest.audit(ws)
    memory.write(ws, result)
    bad = []
    for rows in result["batches"].values():
        bad += [r for r in rows if r.get("current") and r["status"] != "ok"]
    bad += result["untracked"]
    for r in bad:
        extra = f" -> now at {r['found_at']}" if r["status"] == "renamed" else ""
        print(f"  {r['status']:<9} {r['path']}{extra}")
    print("Integrity: " + ", ".join(f"{k} {v}" for k, v in result["counts"].items()) + "  (memory.md updated)")


def cmd_status(a):
    ws = workspace.find()
    print(f"Project {ws.name}  ({ws.root})  key: {'set' if ws.api_key() else 'MISSING'}")
    for b in ws.batches():
        s = memory.status(b)
        print(f"  {b.id:<34} items {s['items']:>3}  approved {s['approved']:>3}  drawn {s['drawn']:>3}  "
              f"ok {s['ok']:>3}  in flight {s['in_flight']:>3}  next: {s['next']}")
    if not ws.batch_ids():
        print("  (no batches) next: ./gbig new <batch-id> --topic \"...\"")


def cmd_memory(a):
    memory.write(workspace.find())
    print("memory.md updated")


def cmd_serve(a):
    from . import server
    try:
        ws = workspace.find()
    except Stop:
        ws = None  # still serve: the page tells the person what to ask their assistant
    server.serve(ws, a.port, not a.no_open)


def cmd_open(a):
    from . import server
    if a.project:
        workspace.use(a.project)
    url = server.open_dashboard(a.port)
    try:
        name = workspace.find(workspace.TOOL_ROOT).name
    except Stop:
        name = "none yet"
    print(f"Dashboard: {url}  (project: {name})")
    print("It keeps running in the background, also after this window closes. Stop it with: ./gbig stop")


def cmd_stop(a):
    from . import server
    print("Dashboard stopped." if server.stop_dashboard() else "The dashboard was not running.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="gbig", description="Gemini Batch image generation, managed locally.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project (default: output/<name>, gitignored)")
    s.add_argument("name")
    s.add_argument("--dir", default="", help="put the project in <dir>/<name> instead, e.g. ~/Pictures")
    s.add_argument("--title", default="", help="display name")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("use", help="select the current project")
    s.add_argument("project", help="name or folder")
    s.set_defaults(fn=cmd_use)

    s = sub.add_parser("projects", help="list projects on this machine")
    s.set_defaults(fn=cmd_projects)

    s = sub.add_parser("config", help="show or change where new projects go")
    s.add_argument("--projects-dir", default="")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("key", help="save (set) or test (check) the Gemini API key")
    s.add_argument("action", choices=["set", "check"])
    s.set_defaults(fn=cmd_key)

    s = sub.add_parser("new", help="create a batch")
    s.add_argument("batch")
    s.add_argument("--topic", required=True)
    s.add_argument("--description", default="")
    s.add_argument("--style", default="", help="text appended to every prompt of the batch")
    s.add_argument("--aspect", default="", help="default aspect ratio, e.g. 16:9")
    s.add_argument("--model", default="")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("import", help="append prompt rows from a CSV/JSON file")
    s.add_argument("batch")
    s.add_argument("file")
    s.add_argument("--replace", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("validate", help="check prompts.csv")
    s.add_argument("batch")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("prompts", help="print the exact prompts Gemini will get")
    s.add_argument("batch")
    s.add_argument("--ids", default="")
    s.set_defaults(fn=cmd_prompts)

    s = sub.add_parser("gen", help="submit / wait for / cancel Gemini Batch jobs")
    s.add_argument("action", choices=["submit", "wait", "cancel"])
    s.add_argument("batch", nargs="?")
    s.add_argument("job", nargs="?")
    s.add_argument("--ids", default="", help="comma-separated item ids (draws them again)")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--yes", action="store_true", help="really send (costs money)")
    s.add_argument("--once", action="store_true")
    s.set_defaults(fn=cmd_gen)

    s = sub.add_parser("audit", help="check MD5 of every generated file; update memory.md")
    s.set_defaults(fn=cmd_audit)

    s = sub.add_parser("status", help="progress of every batch and the next step")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("memory", help="rewrite the tool section of memory.md")
    s.set_defaults(fn=cmd_memory)

    s = sub.add_parser("open", help="open the dashboard (starts it in the background if needed)")
    s.add_argument("project", nargs="?", default="", help="switch to this project first")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_open)

    s = sub.add_parser("stop", help="stop the background dashboard")
    s.set_defaults(fn=cmd_stop)

    s = sub.add_parser("serve", help="run the dashboard in this terminal (open is easier)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    if a.cmd == "gen" and a.action in ("submit", "cancel") and not a.batch:
        p.error("gen submit/cancel needs a batch id")
    if a.cmd == "gen" and a.action == "cancel" and not a.job:
        p.error("gen cancel needs a job name, e.g. job_20261112-101500")
    try:
        a.fn(a)
    except Stop as e:
        print(f"\nSTOP: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
