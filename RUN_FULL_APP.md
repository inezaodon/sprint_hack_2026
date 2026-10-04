# Run the Full Goodwill E-Com Pulse App

This launches the full tabbed application: Daily Pulse, Monthly Dashboard, Ask, Upload, Data & Lineage, Month-End Close, and Data Quality. The older daily-pulse-only server on port `8000` is a different app.

## Run the latest push without changing your checkout

The main checkout may contain local data edits. Keep it untouched and run the fetched branch in a detached worktree instead.

Set these paths for this machine:

```sh
REPO="$HOME/Desktop/SprintHack/sprint_hack_2026"
WORKTREE="/tmp/goodwill-pulse-origin-main"
APP="$WORKTREE/goodwill-pulse"
PYTHON="$REPO/goodwill-pulse/.venv/bin/python"
```

Fetch and create the isolated worktree once:

```sh
git -C "$REPO" fetch origin
git -C "$REPO" worktree add --detach "$WORKTREE" origin/main
```

If that worktree already exists, skip the `worktree add` command. To use a newer push later, create another worktree at a new `/tmp/...` path rather than resetting a worktree that may contain local build output.

Build the full app page:

```sh
cd "$APP"
PYTHONPATH="$APP" "$PYTHON" -m goodwill_pulse.webapp
```

Start the server in that terminal:

```sh
PYTHONPATH="$APP" "$PYTHON" -m uvicorn goodwill_pulse.api:app --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001/>. The server root serves the full tabbed app. Port `8000` remains the older Daily Pulse app. Stop the full app with `Ctrl+C` in its terminal.

If the project virtual environment is missing, create it and install dependencies from the fetched worktree:

```sh
python3.12 -m venv "$REPO/goodwill-pulse/.venv"
"$PYTHON" -m pip install -r "$WORKTREE/requirements.txt" "uvicorn[standard]"
```

## Local runtime notes

- The page uses the synthetic documents bundled with the fetched revision; it does not require Claude sign-in to show the seeded dashboards, lineage, close, or quality data.
- The Ask tab's Claude-backed responses require a configured Anthropic API key or Vercel AI Gateway. Check availability with `curl http://127.0.0.1:8001/api/runtime/available`.
- The local database shim stores viewer changes such as uploads in that browser's local storage.
- Rebuild `web/app.html` with `python -m goodwill_pulse.webapp` after changing the artifact source before starting the app.