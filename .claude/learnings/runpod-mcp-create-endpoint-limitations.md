# RunPod MCP `create-endpoint` tool limitations

**Date:** 2026-08-18

## The issue

The `mcp__runpod__create-endpoint` tool is a **curated projection** of
RunPod's real REST v2 API (`POST /v2/serverless`) — it does not expose
every field the real API accepts. Notably missing: `allowedCudaVersions`.

## What broke because of this

Created a serverless GPU endpoint via the MCP tool for the Docker-image
runner (`ghcr.io/.../mflux-runner:latest`, base image
`nvidia/cuda:13.0.1-...`, requires CUDA 13.0+ host driver). With no way to
constrain `allowedCudaVersions` through the tool, the endpoint accepted
*any* host in the `ADA_24` pool — including hosts whose driver didn't
support CUDA 13. The job landed on an incompatible host and crash-looped
forever (`IN_QUEUE` indefinitely), with every worker failing identically:

```
nvidia-container-cli: requirement error: unsatisfied condition: cuda>=13.0,
please update your driver to a newer version, or use an earlier cuda container
```

This is a **scheduling gap**, not a container/code bug — `list-endpoint-workers`
showed `unhealthy: 0` because the container never even started; the failure
only showed up in `stream-worker-logs` (system source), not job status.

## The fix

Bypass the MCP tool and call the real REST API directly for fields it
doesn't expose:

```bash
curl -s -X POST "https://api.runpod.io/v2/serverless" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "...", "type": "QUEUE",
    "image": "...", "registry": "<containerRegistryAuthId>",
    "disk": 100, "env": {...},
    "gpu": {"pools": ["ADA_24"]},
    "workers": {"min": 0, "max": 1, "idleTimeout": 60},
    "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4},
    "allowedCudaVersions": ["13.0", "13.2"]
  }'
```

Valid CUDA version values per GPU type come from
`GET /v2/catalog/gpus?include=AVAILABILITY&product=SERVERLESS`
(the `mcp__runpod__list-gpu-types` tool's `cudaVersions` field).

## Two traps to remember

1. **Wrong domain.** RunPod has two API hosts and it's easy to mix them up:
   - `api.runpod.ai` — job dispatch/status (`run`, `runsync`, `status`,
     `stream`, `cancel`) for a *specific* endpoint ID.
   - `api.runpod.io` — account-level management (create/list/update/delete
     endpoints, templates, network volumes, etc. — `/v2/serverless`, `/v2/...`).
   `curl https://api.runpod.ai/v2/serverless` returns a plain-text
   `404 page not found`, not a JSON error — easy to misread as "wrong path"
   instead of "wrong host".
2. Any raw REST call needs the account's `RUNPOD_API_KEY` in the command
   line, which the Claude Code auto-mode permission classifier blocks by
   default (reasonably — literal secret + billed infra provisioning). Needs
   an explicit user-added Bash permission rule scoped to the specific host/
   path, e.g. `Bash(curl -s -X POST https://api.runpod.io/v2/serverless:*)`.

## Takeaway

When a RunPod MCP tool call doesn't produce the expected result and the
tool's schema looks like it's missing a field you'd expect the real API to
have, check `https://api.runpod.io/v2/openapi.json` before assuming the
behavior isn't possible — the MCP tool's schema is not the ceiling of what
the API supports.
