"""memory.md: the human-readable record of a project. The tool rewrites only the part between the two
markers; anything a person or agent writes above or below them is kept."""

from __future__ import annotations

from . import manifest
from .util import now, write_text

START = "<!-- gbig:auto:start (written by the tool, do not edit between these markers) -->"
END = "<!-- gbig:auto:end -->"
SKELETON = """# Memory: {name}

Notes for whoever (person or agent) works here next. Write decisions, style choices and open questions here.
The section between the markers is rewritten by the tool after every generate, fetch and audit.

## Notes

- (none yet)

{auto}
"""


def status(batch) -> dict:
    from . import gemini
    man = manifest.load(batch)
    have = manifest.current_by_item(man)
    flying = gemini.in_flight(batch)
    items = batch.items
    counts = {
        "items": len(items),
        "draft": sum(1 for i in items if (i.status or "draft") == "draft"),
        "approved": sum(1 for i in items if i.status == "approved"),
        "drop": sum(1 for i in items if i.status == "drop"),
        "drawn": sum(1 for i in items if i.id in have),
        "to_draw": sum(1 for i in items if i.status == "approved" and i.id not in flying
                       and (i.id not in have or i.result == "redo")),
        "in_flight": len(flying),
        "to_review": sum(1 for i in items if i.id in have and i.status == "approved" and not i.result),
        "ok": sum(1 for i in items if i.result == "ok" and i.id in have),
        "redo": sum(1 for i in items if i.result == "redo"),
    }
    if not items:
        nxt = "Write prompts into prompts.csv (ask your AI assistant: tell it what pictures you need)"
    elif counts["in_flight"]:
        nxt = "Wait: Gemini is drawing. The dashboard checks every minute."
    elif counts["to_draw"]:
        nxt = f"Generate {counts['to_draw']} item(s) from the dashboard"
    elif counts["to_review"]:
        nxt = f"Review {counts['to_review']} new picture(s): mark ok or redo"
    elif counts["draft"]:
        nxt = f"Approve or drop {counts['draft']} draft prompt(s)"
    else:
        nxt = "Done"
    counts["next"] = nxt
    return counts


def render(ws, audit=None) -> str:
    lines = [START, "", f"## Project status (updated {now()})", ""]
    batches = ws.batches()
    if not batches:
        lines += ["No batches yet. Create one with `./gbig new <batch-id> --topic \"...\"`.", ""]
    for b in batches:
        s = status(b)
        lines += [f"### {b.id}: {b.topic or '(no topic)'}", "",
                  f"- Model `{b.model}`, prompts {s['items']} (approved {s['approved']}, draft {s['draft']}, "
                  f"drop {s['drop']}), drawn {s['drawn']}, ok {s['ok']}, redo {s['redo']}",
                  f"- Next: {s['next']}", ""]
        from . import gemini
        js = gemini.jobs(b)
        if js:
            lines += ["| Job | Gemini batch | State | Pictures | Saved | Failed | Submitted |",
                      "|---|---|---|---|---|---|---|"]
            for j in js:
                lines.append(f"| {j['dir']} | `{j['batch_name']}` | {j.get('state', '')} | {j['count']} | "
                             f"{j.get('ok', '')} | {len(j.get('failed') or [])} | {j.get('submitted_at', '')} |")
            lines.append("")
        rows = (audit or {}).get("batches", {}).get(b.id)
        if rows is None:
            rows = [dict(r, status="not checked", found_at="") for r in manifest.load(b)["files"]]
        cur = [r for r in rows if r.get("current")]
        if cur:
            lines += ["| File | Item | Job | MD5 at generation | Integrity | Now at |", "|---|---|---|---|---|---|"]
            for r in sorted(cur, key=lambda r: (r["item"], r["variant"])):
                now_at = r.get("found_at", "") if r.get("status") == "renamed" else ""
                lines.append(f"| {r['file']} | {r['item']} | {r['job']} | `{r['md5']}` | {r['status']} | {now_at} |")
            old = len(rows) - len(cur)
            if old:
                lines.append(f"\n{old} earlier drawing(s) kept in images/legacy/ (see manifest.json).")
            lines.append("")
    if audit:
        c = audit["counts"]
        lines += [f"## Integrity check ({audit['checked_at']})", "",
                  ", ".join(f"{k} {v}" for k, v in c.items()), ""]
        for u in audit["untracked"]:
            lines.append(f"- untracked: {u['path']} (`{u['md5']}`)")
        if audit["untracked"]:
            lines.append("")
    lines.append(END)
    return "\n".join(lines)


def write(ws, audit=None) -> None:
    path = ws.memory_path
    auto = render(ws, audit or manifest.audit(ws))
    if not path.exists():
        write_text(path, SKELETON.format(name=ws.name, auto=auto))
        return
    text = path.read_text(encoding="utf-8")
    if START in text and END in text:
        head, _, rest = text.partition(START)
        _, _, tail = rest.partition(END)
        text = head + auto + tail
    else:
        text = text.rstrip() + "\n\n" + auto + "\n"
    write_text(path, text)
