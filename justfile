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
# Starts it in the background first if it's not already running (on whatever
# port it's already up on if so, else 8000); only stops it again afterward
# if this recipe was the one that started it.
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

# Fetch one Orchestrator endpoint as raw JSON. Resolves the current
# mflux-orchestrator URL fresh each call (via scripts/resolve_orchestrator_url.py
# -- it changes on every redeploy) and echoes the literal curl command to
# stderr first, so `just health` etc. show exactly what's being sent.
_fetch path:
    #!/usr/bin/env bash
    set -euo pipefail
    url=$(.venv/bin/python3 scripts/resolve_orchestrator_url.py)
    echo "curl -H 'Authorization: Bearer \$RUNPOD_API_KEY' ${url}{{ path }}" >&2
    curl -s -H "Authorization: Bearer $RUNPOD_API_KEY" "${url}{{ path }}" | .venv/bin/python3 -m json.tool

# GET /health, raw JSON
health: (_fetch "/health")

# GET /models_mflux, raw JSON
models_mflux: (_fetch "/models_mflux")

# GET /models_hf, raw JSON
models_hf: (_fetch "/models_hf")

# GET /models_missing, raw JSON
models_missing: (_fetch "/models_missing")

# GET /model_store, raw JSON
model_store: (_fetch "/model_store")

# GET /report, raw JSON
report: (_fetch "/report")

# Open the current Orchestrator's /docs in your browser (will likely 401, browser can't send the Bearer header)
open:
    #!/usr/bin/env bash
    set -euo pipefail
    url=$(.venv/bin/python3 scripts/resolve_orchestrator_url.py)
    echo "Opening ${url}/docs (will 401 without a Bearer header -- browser can't send one)" >&2
    open "${url}/docs"
