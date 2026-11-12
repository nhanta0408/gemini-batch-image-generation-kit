"""A batch: one folder under batches/ with batch.json (topic, style, model) and prompts.csv (one row per picture
idea). prompts.csv is the only file a person or agent edits by hand; everything else is written by the tool.

    batches/<id>/
      batch.json        topic, description, style, model, aspect ratio
      prompts.csv       id, topic, summary, prompt, status, variants, aspect_ratio, result, notes
      manifest.json     every picture the tool saved: item, job, model, md5 at generation time
      images/           current pictures (<item>.png, or <item>-2.png ... for variants)
      images/legacy/    earlier drawings, moved here by the tool when an item is drawn again
      jobs/             one folder per Gemini Batch job + index.json
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .util import Stop, now, read_json, write_json, write_text
from .workspace import ASPECT_RATIOS, IMAGE_PRICE, IMAGE_SIZES, Workspace

# fix: a quick correction typed during review; it is sent to Gemini ahead of the prompt and wins over it.
# notes: for people only, never sent.
COLUMNS = ["id", "topic", "summary", "prompt", "fix", "status", "variants", "aspect_ratio", "result", "notes"]
EDITABLE = {"topic", "summary", "prompt", "fix", "status", "variants", "aspect_ratio", "result", "notes"}
FIX_LABEL = "PRIORITY CHANGE (follow this even where anything below says otherwise)"
STATUSES = ("draft", "approved", "drop")
RESULTS = ("", "ok", "redo")
ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
MAX_VARIANTS = 8


class Item:
    def __init__(self, row: dict, line: int):
        self.row = {c: (row.get(c) or "").strip() for c in COLUMNS}
        self.line = line

    def __getattr__(self, name):
        if name in ("row", "line"):
            raise AttributeError(name)
        return self.row.get(name, "")

    @property
    def variant_count(self) -> int:
        try:
            return max(1, min(MAX_VARIANTS, int(self.row["variants"] or 1)))
        except ValueError:
            return 1

    def to_dict(self) -> dict:
        return dict(self.row)


class Batch:
    def __init__(self, ws: Workspace, path: Path):
        self.ws = ws
        self.dir = path
        self.id = path.name
        self.meta = read_json(path / "batch.json", {}) or {}
        self.items = self._load()

    # ---- paths ----
    @property
    def csv_path(self) -> Path:
        return self.dir / "prompts.csv"

    @property
    def output_dir(self) -> Path:
        return self.dir / "images"

    @property
    def jobs_dir(self) -> Path:
        return self.dir / "jobs"

    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    @property
    def lock(self) -> Path:
        return self.ws.lock_path(self.id)

    # ---- settings with workspace fallbacks ----
    @property
    def topic(self) -> str:
        return self.meta.get("topic", "")

    @property
    def style(self) -> str:
        return self.meta.get("style") or self.ws.default("style")

    @property
    def model(self) -> str:
        return self.meta.get("model") or self.ws.default("model")

    @property
    def image_size(self) -> str:
        return self.meta.get("image_size") or self.ws.default("image_size")

    def aspect_ratio(self, item: Item) -> str:
        return item.aspect_ratio or self.meta.get("aspect_ratio") or self.ws.default("aspect_ratio", "1:1")

    def full_prompt(self, item: Item) -> str:
        """Exactly what Gemini receives: the quick fix first (it overrides everything), the item's prompt, then
        the batch style shared by every picture. A bulleted prompt (- Place: ... / - People: ...) gets the fix and
        the style as bullets too, so it reads as one list."""
        prompt, style, fix = item.prompt.strip(), " ".join(self.style.split()), " ".join(item.fix.split())
        bulleted = prompt.startswith("- ")
        parts = []
        if fix:
            parts.append(f"- {FIX_LABEL}: {fix}" if bulleted else f"{FIX_LABEL}: {fix}")
        parts.append(prompt)
        if style:
            parts.append(f"- Style: {style}" if bulleted else style)
        return ("\n" if bulleted else "\n\n").join(p for p in parts if p)

    def by_id(self) -> dict:
        return {i.id: i for i in self.items}

    # ---- CSV ----
    def _load(self) -> list:
        if not self.csv_path.exists():
            return []
        text = self.csv_path.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        missing = {"id", "prompt"} - set(reader.fieldnames or [])
        if missing:
            raise Stop(f"{self.csv_path} is missing column(s): {', '.join(sorted(missing))}")
        return [Item(row, n) for n, row in enumerate(reader, start=2) if any((v or "").strip() for v in row.values())]

    def save(self) -> None:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for item in self.items:
            w.writerow(item.row)
        write_text(self.csv_path, buf.getvalue())

    def save_meta(self) -> None:
        write_json(self.dir / "batch.json", self.meta)

    def update_item(self, item_id: str, fields: dict) -> Item:
        item = self.by_id().get(item_id)
        if item is None:
            raise Stop(f"No item '{item_id}' in batch {self.id}")
        for k, v in fields.items():
            if k not in EDITABLE:
                raise Stop(f"Field '{k}' cannot be edited")
            item.row[k] = str(v if v is not None else "").strip()
        errors = [e for e in self.validate() if e.startswith(f"{item_id}:")]
        if errors:
            raise Stop("; ".join(errors))
        self.save()
        return item

    def validate(self) -> list:
        errors, seen = [], set()
        for it in self.items:
            where = it.id or f"line {it.line}"
            if not ITEM_ID_RE.match(it.id):
                errors.append(f"{where}: id must be lowercase letters, digits and '-' (max 60)")
            if it.id in seen:
                errors.append(f"{where}: duplicate id")
            seen.add(it.id)
            if not it.prompt:
                errors.append(f"{where}: prompt is empty")
            if it.status and it.status not in STATUSES:
                errors.append(f"{where}: status must be one of {', '.join(STATUSES)}")
            if it.result not in RESULTS:
                errors.append(f"{where}: result must be empty, ok or redo")
            if it.aspect_ratio and it.aspect_ratio not in ASPECT_RATIOS:
                errors.append(f"{where}: aspect_ratio must be one of {', '.join(ASPECT_RATIOS)}")
            if it.variants and (not it.variants.isdigit() or not 1 <= int(it.variants) <= MAX_VARIANTS):
                errors.append(f"{where}: variants must be a number from 1 to {MAX_VARIANTS}")
        if self.image_size not in IMAGE_SIZES:
            errors.append(f"batch.json: image_size must be one of {', '.join(s or '(empty)' for s in IMAGE_SIZES)}")
        if self.model not in IMAGE_PRICE:
            errors.append(f"batch.json: unknown model '{self.model}' (known: {', '.join(IMAGE_PRICE)})")
        return errors


def create(ws: Workspace, batch_id: str, topic: str, description: str = "", style: str = "",
           aspect_ratio: str = "", model: str = "") -> Batch:
    from .workspace import BATCH_ID_RE
    if not BATCH_ID_RE.match(batch_id):
        raise Stop("Batch id: lowercase letters, digits and '-', e.g. 2026-11-office-workspaces")
    path = ws.batches_dir / batch_id
    if path.exists():
        raise Stop(f"Batch {batch_id} already exists")
    if aspect_ratio and aspect_ratio not in ASPECT_RATIOS:
        raise Stop(f"aspect ratio must be one of {', '.join(ASPECT_RATIOS)}")
    if model and model not in IMAGE_PRICE:
        raise Stop(f"unknown model '{model}' (known: {', '.join(IMAGE_PRICE)})")
    path.mkdir(parents=True)
    write_json(path / "batch.json", {
        "id": batch_id, "topic": topic, "description": description, "style": style,
        "model": model, "aspect_ratio": aspect_ratio, "image_size": "", "created_at": now(),
    })
    write_text(path / "prompts.csv", ",".join(COLUMNS) + "\n")
    return Batch(ws, path)


def estimate_usd(model: str, count: int) -> float:
    return round(IMAGE_PRICE.get(model, 0.0) * count / 2, 4)
