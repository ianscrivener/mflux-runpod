# List available recipes
default:
    @just --list

label := "com.ianscrivener.mflux-orchestrator"
plist := env_var('HOME') + "/Library/LaunchAgents/com.ianscrivener.mflux-orchestrator.plist"
root := justfile_directory()

# Run the fast unit test suite (mocked, no live network calls)
test:
    .venv/bin/pytest tests/ -q

# Run the Orchestrator locally in the foreground (dev)
serve:
    uv run uvicorn app.main:app --reload

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

# Hit the live Orchestrator's endpoints and print a one-line summary each
test-api:
    .venv/bin/python3 scripts/test_api.py

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

# GET /models_supported, raw JSON
models_supported: (_fetch "/models_supported")

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
