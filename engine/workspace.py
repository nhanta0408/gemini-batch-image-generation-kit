"""Projects: where a user's prompts and pictures live, kept apart from the tool's source.

Everything a person makes lives in output/ (gitignored); the rest of the repo is source that can be shared:

    output/
      local.json          this machine's projects and the current one
      .gbig/              dashboard pid + server.log
      <project>/          project.json, memory.md (history), batches/, .gbig/runs/*.log

A project can also live in any folder the person picks (`init --dir`, or `config --projects-dir` to change
the default). Paths inside output/ are stored relative to the repo, so the folder can be moved or cloned.

Which project a command acts on, first match wins:
  1. GBIG_PROJECT (a path or a registered name)
  2. the current directory, or one of its parents, is a project
  3. "current" in local.json
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path

from .util import Stop, now, read_json, write_json, write_text

TOOL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = TOOL_ROOT / "templates" / "project"
# GBIG_STATE_DIR moves the per-machine files elsewhere (tests use it so they never touch the real ones).
_STATE = Path(os.environ["GBIG_STATE_DIR"]).resolve() if os.getenv("GBIG_STATE_DIR") else TOOL_ROOT
DATA_ROOT = _STATE / "output"
DEFAULT_PROJECTS_DIR = DATA_ROOT
LOCAL = DATA_ROOT / "local.json"
STATE_DIR = DATA_ROOT / ".gbig"  # dashboard pid + log
TOOL_ENV = _STATE / ".env"
CONFIG_NAME = "project.json"
TOOL_VERSION = "0.2.0"

DEFAULT_MODEL = "gemini-3.1-flash-lite-image"
# Interactive price per image in USD (1K output). The Batch API bills half. Check ai.google.dev/pricing:
# prices change, so the dashboard always labels the figure as an estimate.
IMAGE_PRICE = {
    "gemini-3.1-flash-lite-image": 0.034,
    "gemini-3.1-flash-image": 0.067,
    "gemini-3-pro-image": 0.134,
    "gemini-2.5-flash-image": 0.039,
}
ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
IMAGE_SIZES = ("", "1K", "2K", "4K")
BATCH_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")


# ---- machine-local registry -------------------------------------------------------------------------

def _store(path: Path) -> str:
    """Relative to the repo when inside it, so local.json keeps working after the folder moves."""
    path = Path(path).resolve()
    try:
        return str(path.relative_to(_STATE))
    except ValueError:
        return str(path)


def _load(stored: str) -> Path:
    p = Path(stored).expanduser()
    return p if p.is_absolute() else _STATE / p


def local() -> dict:
    data = read_json(LOCAL, {}) or {}
    data.setdefault("projects", {})
    return data


def save_local(data: dict) -> None:
    write_json(LOCAL, data)


def projects_dir() -> Path:
    stored = local().get("projects_dir")
    return _load(stored) if stored else DEFAULT_PROJECTS_DIR


def set_projects_dir(path: Path) -> Path:
    path = path.expanduser().resolve()
    if _inside_tool(path) and path != DEFAULT_PROJECTS_DIR and DEFAULT_PROJECTS_DIR not in path.parents:
        raise Stop("Pick a folder outside the tool's source (or keep the default output/ folder).")
    path.mkdir(parents=True, exist_ok=True)
    data = local()
    data["projects_dir"] = _store(path)
    save_local(data)
    return path


def _inside_tool(path: Path) -> bool:
    return path == TOOL_ROOT or TOOL_ROOT in path.parents


def registered() -> dict:
    """name -> path for every known project that still exists, plus any folder in the projects dir."""
    out = {}
    for name, p in local()["projects"].items():
        if (_load(p) / CONFIG_NAME).exists():
            out[name] = str(_load(p))
    base = projects_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            if not d.name.startswith(".") and (d / CONFIG_NAME).exists():
                out.setdefault(d.name, str(d))
    return out


def use(name_or_path: str) -> "Workspace":
    ws = _resolve(name_or_path)
    data = local()
    data["projects"][ws.root.name] = _store(ws.root)
    data["current"] = ws.root.name
    save_local(data)
    return ws


def _resolve(name_or_path: str) -> "Workspace":
    known = registered()
    if name_or_path in known:
        return Workspace(Path(known[name_or_path]))
    p = Path(name_or_path).expanduser()
    if (p / CONFIG_NAME).exists():
        return Workspace(p)
    raise Stop(f"No project '{name_or_path}'. Projects: {', '.join(known) or '(none yet: ./gbig init <name>)'}")


class Workspace:
    """One project folder (kept as `Workspace` inside the engine)."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.config = read_json(self.root / CONFIG_NAME, {}) or {}

    # ---- paths ----
    @property
    def name(self) -> str:
        return self.config.get("name") or self.root.name

    @property
    def batches_dir(self) -> Path:
        return self.root / "batches"

    @property
    def memory_path(self) -> Path:
        return self.root / "memory.md"

    @property
    def state_dir(self) -> Path:
        return self.root / ".gbig"

    def lock_path(self, batch_id: str) -> Path:
        return self.state_dir / "locks" / f"{batch_id}.lock"

    # ---- settings ----
    def default(self, name: str, fallback: str = "") -> str:
        return str((self.config.get("defaults") or {}).get(name) or fallback)

    @property
    def env(self) -> dict:
        # The tool's .env serves every project; a project's own .env (optional) overrides it.
        return {**_read_env(TOOL_ENV), **_read_env(self.root / ".env")}

    def setting(self, name: str, fallback: str = "") -> str:
        return os.getenv(name) or self.env.get(name) or fallback

    def api_key(self) -> str:
        return self.setting("GEMINI_API_KEY") or self.setting("GOOGLE_API_KEY")

    def require_key(self) -> str:
        key = self.api_key()
        if not key:
            raise Stop("GEMINI_API_KEY is missing. Run `./gbig key set` (or ask the agent to).")
        return key

    def base_url(self) -> str:
        return self.setting("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")

    # ---- batches ----
    def batch_ids(self) -> list:
        if not self.batches_dir.exists():
            return []
        return sorted(p.name for p in self.batches_dir.iterdir() if (p / "batch.json").exists())

    def batch(self, batch_id: str):
        from .batch import Batch
        path = self.batches_dir / batch_id
        if not BATCH_ID_RE.match(batch_id) or not (path / "batch.json").exists():
            raise Stop(f"No batch called '{batch_id}'. Batches: {', '.join(self.batch_ids()) or '(none yet)'}")
        return Batch(self, path)

    def batches(self) -> list:
        return [self.batch(b) for b in self.batch_ids()]


def _read_env(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def set_key(key: str) -> None:
    """The key goes to the tool's .env (gitignored, mode 600), shared by every project on this machine."""
    key = key.strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,}", key):
        raise Stop("That does not look like a Gemini API key (letters, digits, - and _ only).")
    lines = TOOL_ENV.read_text(encoding="utf-8").splitlines() if TOOL_ENV.exists() else []
    lines = [l for l in lines if not l.strip().startswith("GEMINI_API_KEY=")]
    lines.append(f"GEMINI_API_KEY={key}")
    write_text(TOOL_ENV, "\n".join(lines) + "\n")
    TOOL_ENV.chmod(stat.S_IRUSR | stat.S_IWUSR)


def find(start=None) -> Workspace:
    explicit = os.getenv("GBIG_PROJECT")
    if explicit:
        return _resolve(explicit)
    here = Path(start or Path.cwd()).resolve()
    for d in [here, *here.parents]:
        if (d / CONFIG_NAME).exists():
            return Workspace(d)
    current = local().get("current")
    if current and current in registered():
        return Workspace(Path(registered()[current]))
    raise Stop("No project selected. Create one with `./gbig init <name>` or pick one with `./gbig use <name>`.")


def init(name: str, folder=None, title: str = "") -> Workspace:
    if not PROJECT_NAME_RE.match(name):
        raise Stop("Project name: lowercase letters, digits and '-', e.g. office-wellness")
    if name in registered():
        raise Stop(f"A project called '{name}' already exists at {registered()[name]}")
    target = (Path(folder).expanduser().resolve() / name) if folder else projects_dir() / name
    if _inside_tool(target) and DEFAULT_PROJECTS_DIR not in target.parents:
        raise Stop("Projects cannot live inside the tool's source. Use the default output/ folder or --dir elsewhere.")
    if target.exists() and any(p for p in target.iterdir() if not p.name.startswith(".")):
        raise Stop(f"{target} is not empty. Pick another name or folder.")
    target.mkdir(parents=True, exist_ok=True)
    (target / "batches").mkdir(exist_ok=True)
    write_json(target / CONFIG_NAME, {
        "name": title or name,
        "created_at": now(),
        "tool_version": TOOL_VERSION,
        "defaults": {"model": DEFAULT_MODEL, "aspect_ratio": "1:1", "image_size": "", "style": ""},
    })
    for src in TEMPLATES.iterdir():
        dst = target / src.name.replace("dot-", ".")
        if not dst.exists():
            shutil.copy(src, dst)
            if dst.suffix == ".md":
                dst.write_text(dst.read_text(encoding="utf-8").replace("{{TOOL_ROOT}}", str(TOOL_ROOT)),
                               encoding="utf-8")
    ws = use(str(target))
    from . import memory
    memory.write(ws)
    return ws
