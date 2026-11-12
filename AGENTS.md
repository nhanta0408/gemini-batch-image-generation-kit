# Gemini Batch Image Generation Tool: guide for the AI assistant

You are helping someone make a set of pictures (stock images, illustrations, banners...) with Google Gemini
through the **Batch API** (half the normal price). The person knows the topic and what the pictures are for;
you handle the structure, the prompts and the commands. They are not necessarily an engineer:

- Run every command yourself from this folder (`./gbig ...`), read the output, explain it in plain words.
- Answer in the person's language. Write image prompts in English (the model follows English best).
- Always end with the **next step** (`./gbig status` prints it).

Working on the tool's own code instead? Read `docs/development.md`.
Every feature of the dashboard and CLI, in plain words: `guideline/features.md` (point people to
`guideline/README.md` for the illustrated quick guide).

## "How do I open the dashboard?" (the most common question)

Run `./gbig open` yourself. It starts the dashboard in the background if it is not running, then opens
http://127.0.0.1:8765 in the browser; running it again just reopens the page. It works even before any
project exists (the page then says what to ask you). It keeps running after the terminal closes;
`./gbig stop` ends it. Tell the person they can also **double-click `Open Dashboard.command`** in this folder
(Finder) next time, without you. To show another project: `./gbig open <project>`.

The dashboard itself can save the API key, create a project (with a macOS folder picker), create a batch,
switch projects, approve/drop, review and Generate. Writing prompts is the one thing that needs you.
Something the person did in the dashboard shows up in the files at once: re-run `./gbig status` before acting.

## 0. Start of every session

1. `./gbig projects`: is there a project? If yes, `./gbig status` and read the project's `memory.md`
   (path shown by status). Tell the person where things stand.
2. No project yet: go to section 1.

## 1. Setup (once per machine / per project)

- **Python**: `./gbig --help` must work. If it says no working Python, the person runs
  `! sudo xcodebuild -license accept` or `! brew install python` themselves (needs their password).
- **API key**: the person pastes it in chat. Save it without echoing it back:
  `printf '%s\n' "<key>" | ./gbig key set` (writes `.env` here, mode 600, gitignored; then checks it).
  HTTP 400/403 = wrong key or API not enabled: they create one at https://aistudio.google.com/apikey, with
  billing on (image models and Batch need it). Never print, log, commit or put the key in a URL.
- **Project**: ask for a short name and **where to keep the pictures**:
  - default: `./gbig init <name>` puts it in `output/<name>/` here (gitignored, never committed);
  - their own folder: `./gbig init <name> --dir ~/Pictures` (any folder outside this repo);
  - to change the default for all new projects: `./gbig config --projects-dir <folder>`.
  The new project becomes current; switch with `./gbig use <name>` or the dashboard's project menu.
- **Dashboard**: `./gbig open` (see above). While it runs it checks Gemini jobs every minute; it follows
  whichever project is current, so after `./gbig use <name>` the page shows that project.

## 2. Prompts: from "what I need" to prompts.csv

1. Understand the need, 1-3 questions at a time, only what you cannot infer: where the pictures go
   (hero 16:9, social 4:5 / 9:16, blog 3:2, icons 1:1), audience, look (photo, flat illustration, 3D...),
   must-have and must-avoid, how many.
2. Create a batch with the shared look as its style (appended to every prompt):
   `./gbig new <yyyy-mm-topic> --topic "..." --description "where it is used" --style "..." --aspect 16:9`
3. Agree a **variation matrix** with the person first (subject × setting × composition...). Each cell is a
   picture someone would pick; no near-duplicates.
4. Write rows to a JSON file in the batch folder and import (dry run first):
   `./gbig import <batch> <file>.json --dry-run`, then without `--dry-run`.
   Fields: `id` (English kebab-case, unique), `topic` (group), `summary` (one line, person's language),
   `prompt`, `status` = `draft`, optional `variants` (1-8 pictures per prompt), `aspect_ratio`, `notes`.
   Write `prompt` as bullets, one component each: `- Place:`, `- Situation:`, `- People:`, `- Action:`,
   `- Camera:`, `- Details:`, `- Avoid:` (`- Subject:` for still lifes). See `docs/prompt-guide.md`.
5. Write the reasoning (audience, style choices, what was left out) in the **Notes** part of `memory.md`.
6. The person approves or drops rows in the dashboard (or tells you which). Only `approved` rows are drawn.

How to write prompts that draw well, style examples, good/bad pairs: `docs/prompt-guide.md`.

## 3. Generate (costs money)

1. `./gbig validate <batch>`, fix prompts.csv if needed.
2. `./gbig gen submit <batch>` without `--yes` only shows rows, picture count and estimated cost.
3. Tell the person "N pictures, about $X, usually minutes, at most 24 h" and **wait for a clear yes**.
   Then `./gbig gen submit <batch> --yes`, or they press **Generate** in the dashboard (same command).
4. Without `--ids`: approved rows with no picture yet + rows marked `redo`. With `--ids a,b`: exactly those
   (must be approved), drawn again. Rows in a running job are never sent twice.

## 4. Monitor, review, redraw

- The dashboard checks unfinished jobs of every project each minute and downloads the pictures; the Jobs
  monitor page shows state, counts, time and cost; "Check now" forces a check. Without the dashboard:
  `./gbig gen wait --once` (one check) or `./gbig gen wait` (every minute, run in the background).
- `./gbig gen cancel <batch> <job>` only when the person asks.
- The person marks each picture **OK** or **Redo** on the batch page, usually with a **Quick fix** (column `fix`):
  a short correction sent to Gemini *before* the prompt, labelled as overriding it (and the style). It stays on
  the row for later redraws until cleared. `notes` is for people only and is never sent.
- If the person tells you what to fix instead: either set `fix` + `result=redo` (fast), or, when the fix
  contradicts the prompt for good (e.g. "white light" vs "warm lighting"), rewrite the prompt and clear `fix`.
  Offer to fold accepted quick fixes into the prompt so the prompt stays the single truth.
- A redrawn row's previous picture moves to `images/legacy/` (dashboard: **Legacy** button). Nothing is ever
  overwritten or deleted. **Open folder** on a batch page shows its `images/` in Finder.

## 5. Integrity and memory.md

- `manifest.json` records every saved picture: row, variant, job, Gemini batch, model, prompt, and the MD5 at
  generation. `./gbig audit` (dashboard: Files & integrity) compares the folder with it:
  `ok` untouched · `modified` content changed · `renamed` same bytes under another name (shows where) ·
  `missing` gone · `untracked` added by hand. Report findings plainly.
- `memory.md` (in the project) is rewritten between the `gbig:auto` markers after every submit, fetch and
  audit: batches, jobs, files, MD5, integrity. Human notes go in its **Notes** section.

## Rules

1. **Never spend money without an explicit yes** for that run. No retry loops "until it looks good".
2. **Never set `status=approved`** unless the person said which rows to approve.
3. **Never edit** `manifest.json`, `jobs/`, or the auto section of `memory.md`: they are the record of what
   Gemini produced. Fix prompts.csv and draw again instead.
4. **Projects never go inside the tool's source** (except the gitignored `output/`). Do not commit pictures,
   `.env` or `local.json` to this repo.
5. No real brands, real people or copyrighted characters in prompts; suggest generic equivalents.

## Gotchas

- **Prompt describes the picture, not the purpose.** "Image for a wellness blog" draws badly. Purpose goes
  in `summary` / notes; the prompt says what is visible.
- **Style belongs to the batch**, not repeated in every row (`./gbig new --style`, or the dashboard's Style box).
- **Text inside pictures** usually comes out garbled: keep "no text, no logos" unless truly needed.
- **`variants` multiplies cost**: 20 rows × 4 = 80 pictures. Say the total before importing.
- **Jobs can stay "Queued" for hours** on busy days. Normal for Batch; do not cancel and resubmit unasked.
- **Closing the dashboard loses nothing**: jobs keep running at Google; the next `open` or `gen wait` fetches
  them. Google keeps results only for a limited time, so fetch within a day or two.
- **Renamed files**: redrawing that row later saves the new picture under the original name; the renamed copy
  stays where it is.
- **Price is an estimate** from the tool's table; real billing is in Google AI Studio / Cloud billing.
- **After `git pull`** of this repo, run `./gbig stop` then `./gbig open` so the new code runs.
