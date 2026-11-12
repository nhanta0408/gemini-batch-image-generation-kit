"""End-to-end: init -> new -> import -> submit -> fetch -> review -> redraw -> audit, against the fake Gemini.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
STATE = Path(tempfile.mkdtemp(prefix="gbig-test-"))
os.environ["GBIG_STATE_DIR"] = str(STATE)  # before importing engine: keep the real local.json/.env/projects untouched
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tests"))

import fake_gemini  # noqa: E402
from engine import batch as batch_mod  # noqa: E402
from engine import gemini, manifest, memory, workspace  # noqa: E402
from engine.util import Stop  # noqa: E402

PORT = 8798


class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fake_gemini.start(PORT)

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (STATE / ".env").write_text(f"GEMINI_API_KEY=fake-key-0123456789abcdef\n"
                                    f"GEMINI_BASE_URL=http://127.0.0.1:{PORT}\n")
        self.ws = workspace.init("my-images", self.tmp, "Test images")
        self.ws = workspace.find()
        self.b = batch_mod.create(self.ws, "office-desks", "Office desks", style="Photorealistic, soft light.",
                                  aspect_ratio="16:9")
        rows = [
            {"id": "desk-morning", "topic": "desk", "summary": "Desk at sunrise", "prompt": "A tidy desk at sunrise",
             "status": "approved", "variants": "2"},
            {"id": "desk-night", "topic": "desk", "summary": "Desk at night", "prompt": "A desk at night, lamp on",
             "status": "approved"},
            {"id": "desk-fail", "summary": "Blocked", "prompt": "FAIL this one", "status": "approved"},
            {"id": "desk-draft", "summary": "Not yet", "prompt": "A draft idea", "status": "draft"},
        ]
        src = self.tmp / "rows.json"
        src.write_text(json.dumps(rows))
        self.run_cli("import", "office-desks", str(src))
        self.b = self.ws.batch("office-desks")

    def tearDown(self):
        shutil.rmtree(self.tmp)
        workspace.LOCAL.unlink(missing_ok=True)

    def run_cli(self, *args):
        out = subprocess.run([sys.executable, str(TOOL / "gbig.py"), *args], capture_output=True, text=True,
                             env=dict(os.environ))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout

    def test_full_cycle(self):
        plan, _ = gemini.plan(self.b)
        self.assertEqual([i.id for i in plan], ["desk-morning", "desk-night", "desk-fail"])
        self.assertIn("Photorealistic, soft light.", self.b.full_prompt(self.b.by_id()["desk-night"]))
        bulleted = batch_mod.Item({"id": "x", "prompt": "- Place: a desk\n- Action: typing"}, 0)
        self.assertEqual(self.b.full_prompt(bulleted),
                         "- Place: a desk\n- Action: typing\n- Style: Photorealistic, soft light.")
        # The quick fix goes first and says it wins; notes never reach Gemini.
        fixed = batch_mod.Item({"id": "x", "prompt": "- Place: a desk", "fix": "white light", "notes": "ask Lan"}, 0)
        self.assertEqual(self.b.full_prompt(fixed),
                         f"- {batch_mod.FIX_LABEL}: white light\n- Place: a desk\n- Style: Photorealistic, soft light.")
        self.assertNotIn("ask Lan", self.b.full_prompt(fixed))
        self.b.update_item("desk-night", {"fix": "make it brighter"})
        self.assertTrue(self.ws.batch("office-desks").full_prompt(
            self.ws.batch("office-desks").by_id()["desk-night"]).startswith(batch_mod.FIX_LABEL))
        self.b.update_item("desk-night", {"fix": ""})

        dry = self.run_cli("gen", "submit", "office-desks")
        self.assertIn("4 pictures", dry)
        self.assertFalse(gemini.jobs(self.b), "without --yes nothing is sent")

        self.run_cli("gen", "submit", "office-desks", "--yes")
        self.assertEqual(len(gemini.jobs(self.b)), 1)
        self.assertEqual(gemini.in_flight(self.b), {"desk-morning", "desk-night", "desk-fail"})
        self.assertEqual(gemini.plan(self.b)[0], [], "items in flight are not sent twice")

        gemini.refresh(self.b)  # first poll: pending
        self.assertFalse(gemini.jobs(self.b)[0]["fetched"])
        gemini.refresh(self.b)  # second poll: succeeded, downloaded
        job = gemini.jobs(self.b)[0]
        self.assertTrue(job["fetched"])
        self.assertEqual(job["ok"], 3)
        self.assertEqual(job["failed"], ["desk-fail__v1"])
        b = self.ws.batch("office-desks")
        for f in ("desk-morning.png", "desk-morning-2.png", "desk-night.png"):
            self.assertTrue((b.output_dir / f).exists(), f)

        mem = self.ws.memory_path.read_text()
        self.assertIn("desk-morning-2.png", mem)
        self.assertIn("| ok |", mem)
        self.assertIn("## Notes", mem)

        # The failed one is the only thing left to draw.
        self.assertEqual([i.id for i in gemini.plan(b)[0]], ["desk-fail"])

        # Redraw: old picture moves to images/legacy/, new rev recorded, review result reset.
        b.update_item("desk-night", {"result": "redo"})
        self.assertIn("desk-night", [i.id for i in gemini.plan(b)[0]])
        gemini.submit(b, ["desk-night"])
        gemini.refresh(b)
        gemini.refresh(b)
        b = self.ws.batch("office-desks")
        man = manifest.load(b)
        night = [r for r in man["files"] if r["item"] == "desk-night"]
        self.assertEqual([(r["rev"], r["current"]) for r in night], [(1, False), (2, True)])
        self.assertTrue((b.dir / night[0]["file"]).exists())
        self.assertTrue(night[0]["file"].startswith("images/legacy/"))
        self.assertEqual(b.by_id()["desk-night"].result, "")

        # Integrity: edit one, rename one, delete one, add one by hand.
        (b.output_dir / "desk-morning.png").write_bytes(b"edited")
        (b.output_dir / "desk-morning-2.png").rename(b.output_dir / "hero-final.png")
        (b.output_dir / "desk-night.png").unlink()
        shutil.copy(b.output_dir / "hero-final.png", self.ws.root / "copy-elsewhere.png")
        (b.output_dir / "extra.png").write_bytes(fake_gemini.png("extra"))
        a = manifest.audit(self.ws)
        status = {(r["item"], r["variant"]): (r["status"], r["found_at"])
                  for r in a["batches"]["office-desks"] if r["current"]}
        self.assertEqual(status[("desk-morning", 1)][0], "modified")
        self.assertEqual(status[("desk-morning", 2)], ("renamed", "batches/office-desks/images/hero-final.png"))
        self.assertEqual(status[("desk-night", 1)][0], "missing")
        self.assertEqual([u["path"] for u in a["untracked"]], ["batches/office-desks/images/extra.png"])
        out = self.run_cli("audit")
        self.assertIn("renamed", out)
        self.assertIn("modified 1", out)
        self.assertIn("hero-final.png", self.ws.memory_path.read_text())

    def test_memory_keeps_human_notes(self):
        text = self.ws.memory_path.read_text().replace("- (none yet)", "- Use warm light only")
        self.ws.memory_path.write_text(text)
        memory.write(self.ws)
        self.assertIn("- Use warm light only", self.ws.memory_path.read_text())

    def test_validation(self):
        with self.assertRaises(Stop):
            self.b.update_item("desk-night", {"status": "maybe"})
        with self.assertRaises(Stop):
            gemini.plan(self.b, ["desk-draft"])  # drafts are never drawn
        self.assertEqual(self.ws.batch("office-desks").by_id()["desk-night"].status, "approved")

    def test_cancel(self):
        gemini.submit(self.b)
        job = gemini.jobs(self.b)[0]["dir"]
        gemini.cancel(self.b, job)
        gemini.refresh(self.b)
        j = gemini.jobs(self.b)[0]
        self.assertEqual((j["state"], j["fetched"]), ("JOB_STATE_CANCELLED", True))
        self.assertEqual(len(gemini.plan(self.b)[0]), 3, "cancelled items can be sent again")


class Projects(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)
        workspace.LOCAL.unlink(missing_ok=True)

    def test_default_dir_custom_dir_and_switching(self):
        a = workspace.init("alpha")
        self.assertEqual(a.root, (STATE / "output" / "alpha").resolve())
        self.assertEqual(workspace.local()["projects"]["alpha"], "output/alpha", "stored relative: portable")
        b = workspace.init("beta", self.tmp / "Pictures")
        self.assertEqual(b.root, (self.tmp / "Pictures" / "beta").resolve())
        self.assertEqual(workspace.find(self.tmp).root, b.root, "the newest project is current")
        workspace.use("alpha")
        self.assertEqual(workspace.find(self.tmp).root, a.root)
        self.assertEqual(set(workspace.registered()), {"alpha", "beta"})
        with self.assertRaises(Stop):
            workspace.init("alpha")
        with self.assertRaises(Stop):
            workspace.init("inside", workspace.TOOL_ROOT / "engine")  # never inside the source
        workspace.set_projects_dir(self.tmp / "elsewhere")
        c = workspace.init("gamma")
        self.assertEqual(c.root, (self.tmp / "elsewhere" / "gamma").resolve())
        shutil.rmtree(STATE / "output")

    def test_projects_folder_is_gitignored(self):
        ignored = (TOOL / ".gitignore").read_text().split()
        for entry in ("output/", ".env"):
            self.assertIn(entry, ignored)


class OpenDashboard(unittest.TestCase):
    """`./gbig open` is what every end user runs: it must work before any project exists, twice in a row,
    and `./gbig stop` must end it."""

    def gbig(self, *args):
        env = {**os.environ, "BROWSER": "true"}  # no real browser tab during tests
        env.pop("GBIG_PROJECT", None)
        return subprocess.run([sys.executable, str(TOOL / "gbig.py"), *args], capture_output=True, text=True,
                              env=env, cwd=str(TOOL))

    def test_open_without_project_then_again_then_stop(self):
        import urllib.request
        workspace.LOCAL.unlink(missing_ok=True)
        port = "8797"
        try:
            first = self.gbig("open", "--port", port)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("project: none yet", first.stdout)
            html = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
            token = html.split('name="gbig-token" content="')[1].split('"')[0]
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/overview", headers={"X-GBIG-Token": token})
            self.assertIsNone(json.loads(urllib.request.urlopen(req).read())["workspace"], "welcome page data")
            for asset in ("app.js", "style.css"):  # the page is blank without them
                self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{port}/static/{asset}").status, 200)
            second = self.gbig("open", "--port", port)
            self.assertEqual(second.returncode, 0, second.stderr)
        finally:
            self.assertIn("stopped", self.gbig("stop").stdout)


class DashboardActions(unittest.TestCase):
    """What the dashboard buttons call: save key, create project, create batch."""

    @classmethod
    def setUpClass(cls):
        cls.fake = fake_gemini.start(PORT + 2)

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()

    def setUp(self):
        from engine import server
        self.tmp = Path(tempfile.mkdtemp())
        (STATE / ".env").write_text(f"GEMINI_BASE_URL=http://127.0.0.1:{PORT + 2}\n")
        workspace.LOCAL.unlink(missing_ok=True)
        self.app = server.App(None, 0)

    def tearDown(self):
        shutil.rmtree(self.tmp)
        workspace.LOCAL.unlink(missing_ok=True)
        (STATE / ".env").unlink(missing_ok=True)

    def test_key_project_batch(self):
        self.assertIsNone(self.app.overview()["workspace"])
        with self.assertRaises(Stop):
            self.app.save_key({"key": "short"})
        self.app.save_key({"key": "fake-key-0123456789abcdef"})
        self.assertIn("GEMINI_API_KEY=fake-key-0123456789abcdef", (STATE / ".env").read_text())
        self.app.create_project({"name": "blog-pictures", "title": "Blog pictures", "dir": str(self.tmp)})
        ov = self.app.overview()
        self.assertEqual(ov["workspace"]["name"], "Blog pictures")
        self.assertTrue(ov["key_set"])
        with self.assertRaises(Stop):
            self.app.create_project({"name": "blog-pictures"})
        r = self.app.create_batch({"id": "2026-11-desk", "topic": "Desk", "aspect_ratio": "16:9", "style": "Warm."})
        b = self.app.fresh().batch(r["id"])
        self.assertEqual((b.topic, b.meta["aspect_ratio"], b.style), ("Desk", "16:9", "Warm."))
        with self.assertRaises(Stop):
            self.app.create_batch({"id": "Bad Id", "topic": "x"})


class Server(unittest.TestCase):
    def test_token_and_host_guard(self):
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        from engine import server
        tmp = Path(tempfile.mkdtemp())
        try:
            ws = workspace.init("w", tmp)
            app = server.App(ws, 0)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app))
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{port}"
            html = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn(app.token, html)
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + "/api/overview")
            self.assertEqual(e.exception.code, 403)
            req = urllib.request.Request(base + "/api/overview", headers={"X-GBIG-Token": app.token})
            self.assertEqual(json.loads(urllib.request.urlopen(req).read())["batches"], [])
            evil = urllib.request.Request(base + "/", headers={"Host": "evil.example"})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(evil)
            (ws.root / ".env").write_text("GEMINI_API_KEY=secret-value-123456789012\n")
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/files/.env")
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/files/project.json")
            httpd.shutdown()
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
