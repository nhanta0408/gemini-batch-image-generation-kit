# Working on the tool

Users clone this repo and open it with their own AI assistant, which follows `AGENTS.md`. There is no separate
skill to install.

## Layout

```
AGENTS.md               operating guide for the user's AI assistant (CLAUDE.md just imports it)
gbig, gbig.py           launchers (gbig picks a working Python 3.9+ via scripts/find-python.sh)
engine/
  workspace.py          projects: local.json registry, default/custom folders, .env, prices
  batch.py              batch.json + prompts.csv (the only file people edit), validation
  gemini.py             Batch API: upload JSONL, create job, poll, download, cancel (urllib, no deps)
  manifest.py           manifest.json (file -> job, model, prompt, MD5 at generation) + audit
  memory.py             memory.md auto section between gbig:auto markers
  server.py             localhost dashboard + JSON API + background poller (all projects)
  cli.py                ./gbig commands
web/                    dashboard (plain HTML/CSS/JS, no build step)
templates/project/      files copied into a new project ({{TOOL_ROOT}} is replaced, dot- becomes .)
docs/                   prompt-guide.md (read by the assistant), this file
tests/                  fake_gemini.py (local stand-in for the API) + test_flow.py
.env                   the API key, gitignored
output/                all user data, gitignored: local.json (projects, stored as relative paths), .gbig/
                       (dashboard pid + log), and every default project
```

## Invariants

1. **Standard library only, Python 3.9+.** Nothing to `pip install`.
2. **User data never mixes with source.** Projects live in the gitignored `output/` or outside the repo;
   `init` refuses any other folder inside the repo.
3. **Localhost only.** Server binds 127.0.0.1, rejects other Host headers, requires the per-run token on every
   API call, serves only images from the project (never `.env`, CSV or JSON), CSP `script-src 'self'`.
4. **Never overwrite or delete a generated picture.** Redraws move the old file to `images/legacy/`.
5. **The MD5 recorded at generation never changes.** Audit reports drift; it never "fixes" the manifest.
6. **Nothing that costs money runs without confirmation**: CLI needs `--yes`, the dashboard shows a confirm
   panel with count and estimate.
7. Every write to a batch happens under its file lock (`util.locked`): CLI, dashboard and poller can write
   at the same time.
8. `memory.md`: the tool owns only the text between the markers.
9. UI text goes in with `textContent` / `h()`, never `innerHTML` (prompts are user data).
10. **Relative paths only** in anything committed or shared; local.json stores paths inside the repo relative
    to it, so a clone or a moved folder keeps working.

## Testing

- `python3 -m unittest discover -s tests -v`: fake Gemini, no key, no cost. Tests set `GBIG_STATE_DIR` so
  the real `.env` and `output/` are never touched.
- Dashboard without a key: `python3 tests/fake_gemini.py --port 8799`, then put
  `GEMINI_BASE_URL=http://127.0.0.1:8799` and any 20+ character `GEMINI_API_KEY` in a project's `.env`
  (a project `.env` overrides the tool's). A prompt containing `FAIL` gets no picture back.
- Prices in `engine/workspace.py` (`IMAGE_PRICE`) are estimates; update them when Google changes pricing.
