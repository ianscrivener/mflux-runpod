# List available recipes
default:
    @just --list

label := "com.ianscrivener.mflux-orchestrator"
plist := env_var('HOME') + "/Library/LaunchAgents/com.ianscrivener.mflux-orchestrator.plist"
root := justfile_directory()

# Run the fast unit test suite (mocked, no live network calls)
test:
    .venv/bin/pytest tests/ -q

# Validate every JSON (and JSON Lines) file under data/ and data-hf-sync/ --
# catches a syntax mistake in a hand-edited file (e.g. data-hf-sync/
# models_skipped.json) before it 500s the app instead of after.
json:
    .venv/bin/python3 scripts/validate_json.py

# Run the Orchestrator locally in the foreground (dev). Serves the admin
# web app too, if app/static/ has been built (see webapp-build) -- visit
# http://127.0.0.1:8000/
serve:
    uv run uvicorn app.main:app --reload

# Build the admin web app (webapp/, Svelte + Vite) to app/static/, so
# `just serve`/svc-add serve it at http://127.0.0.1:8000/. Re-run after
# any webapp/src change.
webapp-build:
    cd webapp && npm install && npm run build

# Run the web app's own dev server with hot reload (http://127.0.0.1:5173),
# proxying API calls to :8000 -- run `just serve` in another terminal first.
webapp-dev:
    cd webapp && npm install && npm run dev

# Install + start the Orchestrator as a launchd service (runs at login, restarts on crash,
# binds 127.0.0.1:8000 only). Logs to logs/orchestrator.{out,err}.log in this repo.
# Replaces the old orchestrator-local/ standalone-folder approach (_deprecated/) --
# now runs straight from this checkout, no separate synced copy.
svc-add:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "{{ root }}/logs"
    uv_bin="$(command -v uv)"
    # Boot out any existing registration first so re-running svc-add (e.g.
    # to pick up a plist change) doesn't fail on "already bootstrapped" --
    # tolerate "not currently loaded" (the common case), but only that.
    if launchctl list "{{ label }}" >/dev/null 2>&1; then
        launchctl bootout "gui/$(id -u)/{{ label }}"
    fi
    cat > "{{ plist }}" <<PLIST
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>{{ label }}</string>
        <key>ProgramArguments</key>
        <array>
            <string>$uv_bin</string>
            <string>run</string>
            <string>uvicorn</string>
            <string>app.main:app</string>
            <string>--host</string>
            <string>127.0.0.1</string>
            <string>--port</string>
            <string>8000</string>
        </array>
        <key>WorkingDirectory</key>
        <string>{{ root }}</string>
        <key>RunAtLoad</key>
        <true/>
        <key>KeepAlive</key>
        <true/>
        <key>StandardOutPath</key>
        <string>{{ root }}/logs/orchestrator.out.log</string>
        <key>StandardErrorPath</key>
        <string>{{ root }}/logs/orchestrator.err.log</string>
    </dict>
    </plist>
    PLIST
    launchctl bootstrap "gui/$(id -u)" "{{ plist }}"
    echo "Installed and started {{ label }} -- http://127.0.0.1:8000 (logs in {{ root }}/logs/)"

# Stop + uninstall the launchd service
svc-del:
    #!/usr/bin/env bash
    set -euo pipefail
    # Only skip bootout for the expected "not currently loaded" case (nothing
    # to remove) -- a real launchctl failure (e.g. permissions) still aborts.
    if launchctl list "{{ label }}" >/dev/null 2>&1; then
        launchctl bootout "gui/$(id -u)/{{ label }}"
    fi
    rm -f "{{ plist }}"
    echo "Removed {{ label }}"

# Hit the local Orchestrator's endpoints and print a one-line summary each.
# Always targets port 8000 -- starts it in the background there first if
# nothing's already listening on 8000, and only stops it again afterward if
# this recipe was the one that started it. Does not detect a server already
# running on some other port.
test-api:
    #!/usr/bin/env bash
    set -uo pipefail  # no -e: cleanup below must still run if the check fails
    started_by_us=false
    if ! curl -sf -o /dev/null -m 2 http://127.0.0.1:8000/health 2>/dev/null; then
        echo "Local Orchestrator not running -- starting it..." >&2
        mkdir -p logs
        uv run uvicorn app.main:app --port 8000 > logs/test-api-server.log 2>&1 &
        server_pid=$!
        started_by_us=true
        for _ in $(seq 1 30); do
            curl -sf -o /dev/null -m 1 http://127.0.0.1:8000/health 2>/dev/null && break
            sleep 0.5
        done
    fi
    API_BASE_URL=http://127.0.0.1:8000 .venv/bin/python3 scripts/test_api.py
    exit_code=$?
    if [ "$started_by_us" = true ]; then
        kill "$server_pid" 2>/dev/null || true
    fi
    exit $exit_code

# Fetch one Orchestrator endpoint as raw JSON, against the local dev server
# (see `just serve`). Echoes the literal curl command to stderr first, so
# `just health` etc. show exactly what's being sent. RunPod's since-removed
# deployment used to make this resolve a redeploy-fresh remote URL with a
# Bearer token; there's no deployed endpoint to resolve right now, so this
# is local-only until a new deployment target exists.
_fetch path:
    #!/usr/bin/env bash
    set -euo pipefail
    url="http://127.0.0.1:8000"
    echo "curl ${url}{{ path }}" >&2
    curl -s "${url}{{ path }}" | .venv/bin/python3 -m json.tool

# GET /health, raw JSON
health: (_fetch "/health")

# GET /models_mflux, raw JSON
models_mflux: (_fetch "/models_mflux")

# GET /models_hf, raw JSON
models_hf: (_fetch "/models_hf")

# GET /models_missing, raw JSON
models_missing: (_fetch "/models_missing")

# GET /report, raw JSON
report: (_fetch "/report")

# Open the local Orchestrator's /docs in your browser (see `just serve`)
open:
    open "http://127.0.0.1:8000/docs"

# Sync the GPU worker's code to its standalone HF Space checkout
# (docker-runner-hf/, a separate git repo -- HF Spaces builds a Docker image
# straight from the Space's own repo content, so it needs its own copy of
# whatever app/*.py it imports; no build-context reach-outside-the-repo
# option exists the way GHCR Actions builds had). Explicit file allowlist
# (same convention _deprecated/orchestrator-local's now-removed
# update-orchestrator recipe used) -- only what worker.py actually imports
# (app.runner -> app.models_missing), nothing Orchestrator- or webapp-only
# ever leaks in. Never touches docker-runner-hf/.git or its own
# README.md/Dockerfile/worker.py -- those are authored there directly.
update-docker-runner:
    #!/usr/bin/env bash
    set -euo pipefail
    dest=docker-runner-hf
    mkdir -p "$dest/app"
    cp app/__init__.py app/runner.py app/models_missing.py app/outbox.py "$dest/app/"
    echo "Synced worker code to $dest/app/ -- review with 'git -C $dest status', commit+push there when ready"

# Sync (update-docker-runner) then commit + push docker-runner-hf/ to the HF
# Space, triggering a rebuild there. msg is the commit message -- pass one
# that says what actually changed, e.g. `just hf-push msg="bump mlx pin"`.
# No-ops cleanly (exit 0, nothing pushed) if there's nothing to commit after
# the sync, so it's always safe to run.
hf-push msg="Update GPU worker": update-docker-runner
    #!/usr/bin/env bash
    set -euo pipefail
    cd docker-runner-hf
    # docker-runner-hf/ only ever holds files meant to be committed here --
    # the update-docker-runner-synced app/*.py, and Dockerfile/worker.py/
    # README.md authored directly in this repo -- so `git add -A` staging
    # "everything changed" is intentional, not a wildcard shortcut. But a
    # stray untracked file (a debug script, a local secrets file) landing
    # in this checkout would get swept in the same way, so abort instead of
    # silently pushing it if anything outside that known set shows up.
    unexpected=$(git status --porcelain | awk '{print $2}' | grep -Ev '^(Dockerfile|worker\.py|README\.md|\.gitignore|app/)' || true)
    if [ -n "$unexpected" ]; then
        echo "Refusing to push -- unexpected path(s) in docker-runner-hf/, review before committing:" >&2
        echo "$unexpected" >&2
        exit 1
    fi
    git add -A
    if git diff --cached --quiet; then
        echo "Nothing to push -- docker-runner-hf/ has no changes after sync."
        exit 0
    fi
    git commit -m {{ quote(msg) }}
    git push
    echo "Pushed to the HF Space -- rebuild should start automatically."
