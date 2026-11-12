# Feature guide

Everything the kit does, for people and for AI assistants. For a short walkthrough with screenshots, see the
[quick guide](README.md). Assistants should also follow the rules in [`AGENTS.md`](../AGENTS.md).

Contents: [Concepts](#concepts) · [Dashboard](#dashboard) · [Statuses](#statuses) · [Prompts](#prompts) ·
[Generating](#generating) · [Reviewing and Quick fix](#reviewing-and-quick-fix) · [Files and history](#files-and-history) ·
[Folders](#folders) · [Commands](#commands) · [Safety and privacy](#safety-and-privacy) · [Troubleshooting](#troubleshooting)

## Concepts

| Word | Meaning |
|---|---|
| **Project** | One body of work (for example "Blog pictures"). Holds batches, history and logs. |
| **Batch** | One set of images that share a topic, a look and a default shape. |
| **Prompt** (row) | One image idea. It has an id, a short summary, the prompt text and a status. |
| **Look** (style) | Text added to the end of every prompt in a batch, so all images match. |
| **Job** | One request to the Gemini Batch API. One Generate press = one job, whatever the number of images. |
| **Quick fix** | A short correction typed during review. Sent before the prompt, and it wins over the prompt. |
| **Legacy** | Earlier versions of an image that was drawn again. Never deleted. |

## Dashboard

Open it with **`Open Dashboard.command`** (double-click) or `./gbig open`. It runs at
`http://127.0.0.1:8765` in the background and keeps running after the terminal closes. `./gbig open` is safe
to run again: it only reopens the page. `./gbig stop` ends it. Run `./gbig open <project>` to open a
specific project.

| Page | What it is for |
|---|---|
| **Welcome** | Shown before any project exists: *Create project* and *Add Gemini key*. |
| **Batches** | The current project: its batches, *New batch*, project menu, *+ New project*. |
| **Batch** | Prompts, statuses, images, review, *Generate*, *Open folder*, *Legacy*, the batch look and settings. |
| **Jobs monitor** | Every Gemini job of the project: state, counts, time, cost estimate, *Check now*, *Cancel*, logs. |
| **Files & integrity** | Every image compared with its original MD5. |

The top bar shows the current project (a menu when there are several), *+ New project*, the three pages,
and the key status (click it to add or replace the key).

The dashboard reads the files on every request. Anything your assistant changes (new prompts, a new project,
a switched project) shows up after a refresh, and the page refreshes itself every 10 to 15 seconds.

## Statuses

Each prompt moves through:

| Status | Meaning | How it gets there |
|---|---|---|
| **Draft prompt** | Written, not approved. Never sent to Gemini. | New prompts start here. *Back to draft*. |
| **Approved prompt** | Will be drawn on the next Generate. | *Approve prompt* (one row, or selected rows). |
| **Generating** | In a Gemini job right now. | After Generate. |
| **Generated** | The image is here. | When the job finishes and is downloaded. |
| **Dropped** | Kept, but hidden from Generate. | *Drop*. *Restore* brings it back to draft. |

On top of that, a generated image can be marked **OK** or **Redo**. *Redo* means "draw this one again on the
next Generate".

## Prompts

Prompts are usually written by your AI assistant. You can also edit them with **Edit prompt**.

- Write prompts as bullets, one part per line, in this order (leave out what does not apply):
  `- Place:` `- Situation:` `- People:` `- Action:` `- Camera:` `- Details:` `- Avoid:`.
  Use `- Subject:` instead of People and Action for objects and products.
- The batch **Look** is added as a last `- Style:` line. Put light, colours, photo or illustration style there,
  once, instead of in every prompt.
- **Full prompt sent to Gemini** shows exactly what is sent: Quick fix (if any), the prompt, then the look.
- **Pictures** (1 to 8) draws several versions of the same prompt. It multiplies the cost.
- **Shape** per prompt overrides the batch default (1:1, 4:5, 3:2, 16:9, 9:16 and more).
- **Notes** (in Edit prompt) are for people only and are never sent to Gemini.

Tips for prompts that draw well are in [`docs/prompt-guide.md`](../docs/prompt-guide.md). Short version:
describe what is visible (not what the image is for), avoid text inside images, and avoid real brands or
real people.

## Generating

1. Approve the prompts you want.
2. Press **Generate N** (N = approved prompts without an image, plus prompts marked Redo), or select rows
   and press **Generate** on the selection.
3. A panel shows the **number of images** and the **estimated cost**. Nothing is sent yet.
4. **Send to Gemini** starts one Batch job. **Cancel** closes the panel.

About the Batch API:

- It costs **half** of a normal Gemini call. The price shown is an estimate from the kit's price table; your
  real bill is in Google AI Studio.
- It usually takes minutes, at most 24 hours. Prompts already in a running job are never sent twice.
- While the dashboard runs, it checks every unfinished job of **every** project once a minute and downloads
  finished images. *Check now* checks immediately. Without the dashboard: `./gbig gen wait`.
- *Cancel* (Jobs monitor) stops a job that has not finished. Its prompts can be sent again later.
- A prompt Gemini refuses (for example a safety block) shows as *failed* on the job and can be generated again.

## Reviewing and Quick fix

For each generated image:

- **Click the image** to open it full size.
- **OK** when you are happy.
- **Redo** to draw it again on the next Generate.
- **Quick fix**: type what to change, for example *"brighter light, both people smiling"*, then press **Redo**.
  The fix is sent to Gemini **before** the prompt, labelled as a priority change, so it wins when it disagrees
  with the prompt or the look. It stays on the row for later redraws until you clear it. When an image is
  right, ask your assistant to move the fix into the prompt so the prompt stays complete.

## Files and history

- **Open folder** (batch page) opens the batch's `images/` folder in Finder (or your file manager).
- **Legacy** opens `images/legacy/`. When a prompt is drawn again, the previous image moves there as
  `<id>-r1.jpg`, `<id>-r2.jpg`... Nothing is ever overwritten or deleted.
- Every saved image is recorded in the batch's `manifest.json`: which prompt, which job, which model, the exact
  prompt, the time, and the **MD5 at generation**.
- **Files & integrity** compares each file with that MD5:

| Status | Meaning |
|---|---|
| `ok` | The file is exactly what Gemini produced. |
| `modified` | The file was edited or re-saved after generation. |
| `renamed` | The file moved or was renamed; the page shows where it is now. |
| `missing` | The file is gone and no copy with the same content exists. |
| `untracked` | An image in `images/` that no job produced (added by hand). |

- `memory.md` in each project is the human-readable history: notes at the top (yours and your assistant's),
  and a section the kit rewrites after every generate and check: batches, jobs, files, MD5 and integrity.
  Do not edit between the `gbig:auto` markers.

## Folders

```
gemini-batch-image-generation-kit/     the kit (source, shared on GitHub)
  Open Dashboard.command               double-click to open the dashboard
  AGENTS.md                            instructions for AI assistants
  guideline/                           this guide
  .env                                 your Gemini key (never uploaded)
  output/                              all your work (never uploaded)
    local.json                         your projects and the current one
    <project>/
      memory.md                        history and notes
      project.json                     name and defaults
      batches/<batch>/
        batch.json                     topic, look, model, default shape
        prompts.csv                    the prompts and their statuses
        manifest.json                  record of every saved image (do not edit)
        images/                        current images
        images/legacy/                 earlier versions
        jobs/                          Gemini job files (do not edit)
```

Paths inside `output/` are stored relative to the kit folder, so you can move or copy the whole folder.
A project created in another folder (`Choose folder`) keeps the same layout there.

## Commands

The dashboard and your assistant cover almost everything. The command line, run from the kit folder:

| Command | What it does |
|---|---|
| `./gbig open [project]` | Open the dashboard; start it in the background if needed. |
| `./gbig stop` | Stop the dashboard. |
| `./gbig key set` / `key check` | Save the Gemini key (reads it from the keyboard or a pipe) / test it. |
| `./gbig init <name> [--dir <folder>] [--title "..."]` | Create a project (default in `output/`). |
| `./gbig use <name>` / `projects` | Switch the current project / list projects. |
| `./gbig config --projects-dir <folder>` | Change where new projects go. |
| `./gbig new <batch> --topic "..." [--style "..."] [--aspect 16:9] [--model ...]` | Create a batch. |
| `./gbig import <batch> <file.json\|file.csv> [--dry-run] [--replace]` | Add prompts from a file. |
| `./gbig validate <batch>` / `prompts <batch>` | Check prompts.csv / print the exact prompts. |
| `./gbig gen submit <batch> [--ids a,b] [--yes]` | Generate. Without `--yes` it only shows count and cost. |
| `./gbig gen wait [<batch>] [--once]` | Check jobs and download results (every minute, or once). |
| `./gbig gen cancel <batch> <job>` | Cancel a running job. |
| `./gbig status` | Progress of every batch and the next step. |
| `./gbig audit` | Check every file's MD5 and update `memory.md`. |

Prompt files for `import` are a JSON list (or CSV) of rows with: `id` (lowercase, dashes), `topic`,
`summary`, `prompt`, `status` (`draft`), and optionally `variants`, `aspect_ratio`, `fix`, `notes`.

## Safety and privacy

- The dashboard only listens on your own computer (`127.0.0.1`). Other websites cannot use it.
- Your key is stored in `.env` in the kit folder, readable only by you, and never uploaded by git.
- Only the prompts are sent to Google Gemini. Images and history stay on your computer.
- Nothing costs money until you press **Send to Gemini** (or run `gen submit --yes`).

## Troubleshooting

| Problem | Fix |
|---|---|
| The page does not open | Run `./gbig open` in the kit folder and read the message. |
| "No working Python" | macOS: run `sudo xcodebuild -license accept`, or install Python with `brew install python`. |
| "Gemini did not accept this key" | Copy the key again from Google AI Studio and check that billing is on. |
| A job stays "Queued" for hours | Normal on busy days for the Batch API (up to 24 hours). Do not resend it. |
| The page looks old after an update | Run `./gbig stop`, then `./gbig open`. |
| SSL certificate error | On a python.org install, run "Install Certificates.command" in `/Applications/Python 3.x/`. |
| A file shows `renamed` or `modified` | Nothing is broken; it only tells you the file is not the original anymore. |
