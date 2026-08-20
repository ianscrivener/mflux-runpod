# PRD — Admin Web App

Local-only admin/monitor UI for the Orchestrator, replacing ad-hoc Swagger-UI
clicking for day-to-day use. Design mockup: see the published `MFlux Models`
Artifact from this session (table layout, stats comparison, IBM Plex
Mono/Sans on warm-brass/cool-graphite tokens) — this doc covers scope and
plumbing, not visual spec.

## Goals

- Replace Swagger UI as the day-to-day way to check catalog status, review
  the queue, trigger/cancel builds, and watch run history.
- Reactive: state changes (a build finishing, a queue edit) should be visible
  without a manual page reload.
- Cheap to build and maintain — this is a personal tool for one operator, not
  a product.

## Non-goals

- **No auth.** Runs on `127.0.0.1` only, single operator (you), on your own
  machine. Never expose this beyond localhost.
- **No mobile/responsive polish.** Desktop browser, your machine, your window
  size.
- **No SSR, no routing, no build-your-own-design-system.** See tech stack.

## Users

One: you, running it locally alongside (or instead of) `just serve`'s bare
API.

## Tech stack (decided this session)

- **Svelte 5 (runes), not SvelteKit.** No SSR/multi-page/SEO need — the
  backend already exists as a separate FastAPI service, so Kit's server-side
  features are pure overhead. Runes (`$state`/`$derived`/`$effect`) give
  fine-grained reactivity without a separate state library.
- **Vite** for dev server + build (`npm create vite@latest`, Svelte template).
- **Serving model**: `vite build` → static files → FastAPI serves them via
  `StaticFiles` mount at `/`. One process, one port, no CORS. `just serve`
  starts everything. For active frontend dev, run Vite's own dev server with
  `server.proxy` forwarding API calls to `:8000` — dev-time convenience only,
  not part of the shipped app.
- **Live updates: polling, not WebSockets/SSE.** `setInterval` + `fetch`
  against `/report`, `/models_queue`, `/datasets` every few seconds. The API
  has no push mechanism and doesn't need one yet for this scale — revisit
  only if polling actually feels laggy in practice.
- **No router.** A handful of views switched by a reactive `$state` variable.
  Add `svelte-routing` (or similar) only if the view count actually grows
  past what that comfortably handles.
- **Styling**: hand-written CSS following the design mockup's token system
  (no Tailwind, no component library) — small enough surface area that a
  framework buys nothing.

## Views

### 1. Models (`GET /models_mflux`, `GET /models_hf`, `GET /models_missing`)

The table from the design mockup: `#`, family, model name, src link
(`hf_model_name`), mflux link (published Collection), GB, text encoder, and
one column per quant (`q3`/`q4`/`q5`/`q6`/`q8`/`bf16`) — ✅ if published,
otherwise a checkbox. Stats strip up top: MFlux-supported vs.
Hugging-Face-published counts (families/models/quants).

**Gap**: `family`, `GB`, and `text_encoder` aren't fields anywhere in the API
today — `data-hf-sync/models_mflux.json` has `model_family`/`model_sub_family`
but no upstream size or text-encoder-name field, and `configs/models/*.yaml`
doesn't carry them either. Either derive `family` from the existing
`model_family` field (straightforward) and leave GB/text-encoder blank until
that data exists somewhere, or scope adding them as a small follow-up. Not
blocking — the mockup used illustrative values for exactly this reason.

**Checkbox action**: checking an unbuilt quant should call
`POST /models_queue` (add that model+quant to the queue) — see View 3, not a
direct `/generate` dispatch. Keeps "queue it" and "actually build it" as
separate, deliberate steps.

### 2. Missing (`GET /models_missing`)

Simple list/table of what's missing vs. complete. Mostly redundant with the
quant-checkbox view in #1 — may not need to be a separate page; worth
deciding once #1 is built and it's clear whether the combined view already
covers this.

### 3. Queue (`GET/POST/PATCH/DELETE /models_queue`)

List, add, edit (status/quants/note), delete queue entries. Status pill
(`pending`/`approved`/`skipped`), inline edit, delete confirmation (even
without auth, a delete should still ask — it's irreversible).

**Real gap, not just a UI gap**: there is no "process the queue" — no
endpoint that takes an `approved` entry and actually calls
`generate_one`/`dispatch_trigger` for it. Right now the queue is pure
bookkeeping; turning it into real dispatches still means using View 4's
per-model `/generate` trigger by hand. Building that link (a "process queue"
button/endpoint) is the natural next increment after this UI ships, not
something to silently assume exists.

`POST /models_queue/publish` / `POST /models_queue/restore` (HF-bucket
master round-trip, `app/queue_store.py` -- moved off DO Spaces 2026-08-20)
— a small "sync" affordance on this page, not day-to-day-critical.

### 4. Generate / Runs (`POST /generate`, `POST /generate/{run_id}/cancel`, `GET /report`, `DELETE /report`)

Run history table (status, duration, per-quant results), a manual
"generate this series" trigger with the `dispatch`/`force_hf_overwrite`
options, and a cancel button on any `running` row. `DELETE /report` as a
tucked-away "clear log" action, not a prominent button (irreversible,
matches the API's own "intentionally blunt" framing).

### 5. Datasets (`GET /datasets`, `POST /datasets/{name}/pull`, `POST /datasets/{name}/push`)

Sync status for the 8 HF-bucket datasets (last-known hash, local mtime,
writable/not) with manual pull/push buttons per row. Low-traffic page —
mainly useful for "did the last sync actually happen" visibility.

## API dependencies (full list, current as of this session)

```
GET    /models_mflux
GET    /models_hf
POST   /models_hf/update
GET    /models_missing
POST   /models_missing/update
GET    /models_queue
POST   /models_queue
PATCH  /models_queue/{entry_id}
DELETE /models_queue/{entry_id}
POST   /models_queue/publish
POST   /models_queue/restore
GET    /datasets
POST   /datasets/{name}/pull
POST   /datasets/{name}/push
POST   /generate
POST   /generate/{run_id}/cancel
GET    /report
GET    /report/dump
DELETE /report
POST   /outbox/poll
GET    /health
```

Everything the five views need already exists except the `family`/`GB`/
`text_encoder` fields (View 1) and queue processing (View 3) noted above.

## Out of scope for v1

- Queue processing (dispatch-from-queue) — separate follow-up, not this PRD.
- Cost-per-model reporting (needs the deferred `runs`→`logs/*.jsonl` rewiring
  from the HF-dataset-sync work, also not done yet).
- Auth, multi-user, remote access.
- Mobile layout.
