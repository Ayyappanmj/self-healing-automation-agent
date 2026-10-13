# Deployment

The app is designed to run **locally**, where it opens a visible browser you can watch. A public copy has to solve a
problem: browsers are heavy and hosted platforms are small. This page gives a practical strategy and is clear about what is
verified and what is not.

## Why Streamlit Community Cloud cannot run the live demo

1. **You cannot watch the browser.** The browser window opens on the server, not on the viewer's screen. Hosted runs must be headless.
2. **Resources are small and shared.** Streamlit's forum FAQ listed limits of roughly up to 2 CPU cores and about 2.7 GB of
   memory as of February 2024, and says they may change at any time. A Firefox instance plus the app can hit that.
3. **Playwright needs a browser binary and system libraries.** Community Cloud installs Python packages from
   `requirements.txt` and Linux packages from `packages.txt`, but there is no `playwright install` step, and forum users have
   reported difficulty deploying Playwright there. Treat it as unproven for this project.
4. **The disk is ephemeral.** The SQLite history would disappear when the app restarts or sleeps.

I have not deployed this project to Streamlit Community Cloud. The statements above come from Streamlit's documentation and forum.

## Strategy

| Goal | Approach | Status |
|---|---|---|
| Let someone see it working in 30 seconds | A recorded demo GIF/video in the README (`demo-recording.md`) | You record it |
| Let someone click through the real dashboard without installing anything | **Showcase mode** on Streamlit Community Cloud: a read-only copy that displays a **real run exported from your machine** | Implemented and unit-tested; the hosted deployment is untested |
| Prove it works on a clean machine | **GitHub Actions** runs the full suite, including real Firefox, on every push | Workflow provided; first run is yours to check |
| Run the full app on a server | **Docker image** (headless) on any host that runs containers | Dockerfile provided; I have not built it |
| Recruiter wants to run it | `git clone`, then the Windows commands in the README | Works locally |

The project stays fully runnable locally whatever happens to the hosted copy.

## Showcase mode (read-only hosted copy)

Showcase mode never launches a browser and never makes up data: it loads a JSON export of a run you did locally.

1. Run the demo locally: `streamlit run app.py`, click **Run Demo**.
2. In **Settings > Stored data**, click **Export everything (JSON)** and save the file as `showcase\showcase_run.json`.
3. Look at it. It contains your test names, locators and timestamps. Make sure you are happy to publish them.
4. Commit it (`git add showcase/showcase_run.json`) and push.
5. On https://share.streamlit.io create an app from the repository, main file `app.py`.
6. In the app's **Settings > Secrets**, add:
   ```toml
   SHOWCASE_MODE = "true"
   ```
   (Locally you can test it with `$env:SHOWCASE_MODE="true"; streamlit run app.py`.)
7. The hosted app shows a banner ("a real run recorded locally on <date>"), the dashboard, healing explanations, history
   and reports from your export, and disables Run Demo, the DOM inspector and the delete button.

If the file is missing or invalid, the app says so instead of showing empty or fake data. The DOM inspector and the AI
reasoning prompt panels work on stored data only; anything that needs a browser is disabled.

## GitHub Actions

`.github/workflows/tests.yml` installs the dependencies, runs `playwright install --with-deps firefox`, and runs
`pytest --cov` on Python 3.11 and 3.12. It is the evidence, visible to anyone, that the suite passes on a clean machine.
See `github-setup.md` for adding the badge after the first green run.

## Docker (headless, self-hosted)

```powershell
docker build -t self-healing-agent .
docker run --rm -p 8501:8501 -v shdata:/app/data self-healing-agent
```

Then open http://localhost:8501. The browser runs inside the container (`HEADLESS=true`), so Run Demo works but you see the
live log and screenshots, not a window. The `shdata` volume keeps the SQLite history between runs. For a hosted version, any
platform that runs containers can use this image; check its current free-tier memory limits first, since Firefox needs
substantial memory, and I cannot promise any particular free tier works.
