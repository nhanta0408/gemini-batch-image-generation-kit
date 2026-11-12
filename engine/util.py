"""Small helpers shared by every module: atomic writes, file locks, hashing, timestamps."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class Stop(Exception):
    """A problem the user must fix. The CLI prints it without a traceback; the dashboard shows it."""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_text(path: Path, text: str) -> None:
    """Write through a temp file so a crash never leaves half a file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_json(path: Path, data) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


@contextlib.contextmanager
def locked(path: Path):
    """Exclusive lock shared by the CLI, the dashboard and the poller, so two writers never race."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS and not path.name.startswith(".")


def log(message: str = "") -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def env_bool(name: str) -> bool:
    return os.getenv(name, "").lower() in ("1", "true", "yes")
