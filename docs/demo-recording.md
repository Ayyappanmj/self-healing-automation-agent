# Recording the demo

The goal is a short, **real** recording that a recruiter understands in under a minute:
a locator fails, the agent inspects the page, picks a replacement, proves it is right, retries, and succeeds,
and it also refuses when it is not sure. Nothing in the recording may be staged, mocked or edited into something
the app did not do.

## Honesty rules

1. **Record a real run of the app.** No mock-ups, no composited screenshots, no invented numbers.
2. **Show what actually happened.** If a step ends in "manual review", keep it in: refusing to guess is a feature.
3. **Do not call it AI-reasoned unless it was.** With `LLM_PROVIDER=none` the AI panel says "disabled". Say
   "deterministic scoring, AI optional", or enable Ollama and record a second take. Only borderline steps are sent to the model.
4. **Speed-ups must be disclosed.** If you cut idle waiting, say so in the caption ("waiting sections sped up").
5. **Do not show secrets.** Keep the Settings tab closed, no API keys, no private URLs, no personal file paths in the taskbar or title bars.
6. **Put the setup in the caption** (see the template below) so viewers know what they are watching.

## 1. Prepare

```powershell
cd self-healing-automation-agent
.venv\Scripts\activate
python -m scripts.demo_preflight          # checks browser, visible window, LLM state, leftover runs, port
python -m scripts.demo_preflight --reset  # optional: wipes stored runs, demo logs and failure screenshots (asks first)
```

The pre-flight check fails if Firefox is missing or `HEADLESS=true` (no window to record), and warns if old runs would
make the dashboard start non-empty, if Ollama is configured but unreachable, or if the threshold is not the default.
Starting from an empty dashboard is good: the metrics you show are then exactly the ones this recording produced.

Optional, for a real AI-reasoning take: install [Ollama](https://ollama.com/download), run `ollama pull llama3.2`,
and set `LLM_PROVIDER=ollama` in `.env`. Check the **Settings** tab afterwards, but do not record it.

**Screen layout (do this once):**
- Use a 1920x1080 display if you can; close other windows and notifications (Windows: turn on Do Not Disturb).
- Browser zoom 100%. Snap the Streamlit tab to the left half (`Win` + `Left`). The Playwright Firefox window opens
  separately when the run starts: snap it to the right half (`Win` + `Right`).
- Set **Slow motion** to 400 ms on the Run Automation tab so viewers can follow each action.
- Do one unrecorded dry run and time it: the full demo duration depends on your machine, so measure it rather than guess.
- **Expect dead air.** Each broken step first waits for the action timeout (3 s by default) before healing starts. In one local
  headless run on a development machine, each changed UI took about 12 seconds for that reason, while the healing itself took
  tens of milliseconds per step. For a tighter recording set `ACTION_TIMEOUT_MS=1000` in `.env` (restart Streamlit) and say so
  in the caption, or speed up the waiting and disclose it.

## 2. Shot list and script (about one minute)

Record the whole screen region, not a single window, because two windows are involved.

| Time | On screen | What to say (or caption) |
|---|---|---|
| 0:00 | **Dashboard**, empty: metrics show dashes | "UI changes break test scripts. This agent repairs broken locators itself, and only when it is confident." |
| 0:06 | Click **Run Demo** | "I'll run a login test, then run the same old locators against changed versions of the page." |
| 0:10 | **Browser opens**, v1 baseline passes; live log shows green steps | "On the original UI, everything passes. The agent also records what each element looks like." |
| 0:20 | UI v2: a step turns red in the live log: **locator failure** (`button#login` / `locator_not_found`) | "Now the UI changed. The old locator no longer matches anything." |
| 0:26 | Live log: **DOM inspection** line, the **candidate locators** and the **selected locator** with its confidence | "It inspects the live page, generates candidates, scores them, and picks the best." |
| 0:32 | Log line "Healed locator successfully", the step turns green, **retry** succeeds | "It validates that the locator matches exactly one, intended, visible element, then retries the action." |
| 0:38 | A later step (for example the welcome message) ends in **manual review**, or the safety check refuses the missing target | "When it is not sure it does nothing and asks for review. It never guesses." |
| 0:46 | **Self-Healing** tab: pick one event. Show the stage row, candidate table, the explanation, and the **AI reasoning** panel | "Every decision is explained. Here is the AI panel: [disabled, deterministic mode] or [the model's reason and the exact prompt]." |
| 0:54 | **DOM Inspector** (optional, a few seconds): inspect v2, click an element to show its generated locators | "This is what the agent sees." |
| 0:58 | **Healing History** tab, then back to the **Dashboard**: populated metrics | "Everything is stored. These numbers come only from this run." |

Notes:
- The exact outcome of each step (what heals, what is refused) comes from your real run. In one local headless run with
  no LLM, v2 and v4 healed every step, v3 healed three and refused the welcome message, and the missing `#checkout` was
  refused. The deterministic scorer should reproduce that, but record what **you** get and describe that.
- If a step you want to show does not happen, do not recreate it with edits. Adjust the scenario in the app (for
  example choose the versions on the Run Automation tab) and record again.
- For a 30-second cut: skip the DOM Inspector and History, and show only v2 healing plus one refusal.

## 3. Record

Free options on Windows:
- **ScreenToGif** (https://www.screentogif.com): record a region and export a GIF directly, with simple trimming.
  Good for short clips.
- **OBS Studio** (https://obsproject.com): record the screen to MP4, then convert (below). Good for the full minute.
- **Xbox Game Bar** (`Win` + `Alt` + `R`) records only one app window, so it cannot capture both the dashboard and the
  browser together. Avoid it for this demo.

Save raw recordings in `recordings\` (this folder is git-ignored so large files are never committed).

## 4. Make the GIF

A full-minute GIF is large. A good pattern is a **short GIF (15-25 seconds) of the heal-and-refuse moment** in the README,
plus the full MP4 linked or attached.

```powershell
winget install Gyan.FFmpeg                                   # or download ffmpeg from https://ffmpeg.org/download.html
.\scripts\make_gif.ps1 -In recordings\demo.mp4 -Out docs\demo.gif -Start 8 -Duration 25
```

`make_gif.ps1` uses a two-pass palette so colours stay clean. Options: `-Fps 12`, `-Width 960`, `-Start`, `-Duration`.
It warns if the file is over 10 MB, which is a practical size to aim for in a README. If it is too big, shorten the
clip, lower `-Fps` to 10, or `-Width` to 800. Open the GIF and check that locators and the confidence text are readable.

For the full video, GitHub lets you drag an MP4 into the editor of a README, issue or release and gives you a hosted
link; or upload it as an unlisted video and link it.

## 5. Publish it in the README

1. Confirm the file exists: `docs\demo.gif`.
2. In `README.md`, in the **Demo** section, replace the commented placeholder line with:
   ```markdown
   ![Self-healing demo](docs/demo.gif)
   ```
3. Replace the "has not been added yet" note with a caption (template below).
4. Run `pytest tests/test_docs.py`. The suite refuses to embed `docs/demo.gif` while the file is missing, and checks that
   every link resolves.
5. Commit the GIF with the README change.

**Caption template** (fill in the blanks truthfully):

> Recorded locally on [date], Windows, Firefox, [no LLM / Ollama llama3.2]. A login test passes on UI v1, then the same
> old locators are run against changed UIs. The agent heals what it is confident about and refuses the rest.
> [Waiting sections sped up.] Full video: [link].

## 6. Final checklist

- [ ] `python -m scripts.demo_preflight` shows no `[FAIL]`
- [ ] Dashboard started empty (or the caption says it did not)
- [ ] The recording shows a failure, the candidates and selection, validation/retry, and a success
- [ ] It also shows a refusal ("manual review required")
- [ ] The AI panel is described truthfully (disabled vs. a real model)
- [ ] No keys, private paths or notifications visible
- [ ] GIF is readable and not huge; README caption states the setup
- [ ] No metric appears that the app did not compute
