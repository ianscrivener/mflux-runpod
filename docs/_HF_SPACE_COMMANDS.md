# Hugging Face Space Commands

How to drive a Hugging Face Space (e.g. this project's GPU worker,
`cleverheart2026/mflux-model-gpu-runner-storage` — see
`docker-runner-hf/README.md`) from the outside: CLI first, Python
(`huggingface_hub`) equivalent second. Both use the same `HF_TOKEN`
credential the rest of this project already relies on.

Verified against the installed `huggingface_hub` 1.27.0 (`hf --help`,
`hf spaces --help`, and each subcommand's `--help`) — 2026-08-21.

| Action | CLI (`hf spaces ...`) | Python (`huggingface_hub`) |
|---|---|---|
| **GET machine types** — list available hardware tiers | `hf spaces hardware` | `from huggingface_hub import list_spaces_hardware`<br>`list_spaces_hardware()` |
| **GET status/metadata** — current runtime stage, hardware, sleep time, etc. | `hf spaces info <namespace>/<repo>`<br>(add `--expand runtime,sdk,...` to narrow the fields) | `from huggingface_hub import HfApi`<br>`HfApi().get_space_runtime("<namespace>/<repo>")`<br>(or `space_info(...)` for repo-level metadata, not just runtime) |
| **GET status** (running / asleep / starting etc.) | `hf spaces info <namespace>/<repo> --expand runtime`<br>(read the `stage` field of the returned `runtime` object) | `HfApi().get_space_runtime("<namespace>/<repo>").stage` |
| **POST restart** | `hf spaces restart <namespace>/<repo>`<br>(add `--factory-reboot` to rebuild without the build cache) | `HfApi().restart_space("<namespace>/<repo>", factory_reboot=False)` |
| **POST set pause: on** | `hf spaces pause <namespace>/<repo>` | `HfApi().pause_space("<namespace>/<repo>")` |
| **POST set pause: off** (resume) | `hf spaces restart <namespace>/<repo>`<br>— there is no separate unpause/resume subcommand; `restart` is the documented way to bring a paused Space back | `HfApi().restart_space("<namespace>/<repo>")`<br>— same method as plain restart; per its docstring, "This is the only way to programmatically restart a Space if you've put it on Pause" |
| **POST add bucket to space** | `hf spaces volumes set <namespace>/<repo> -v hf://buckets/<org>/<bucket>:/<mount_path>`<br>(add `:ro` for read-only; **replaces** the Space's whole volume list, it does not append — repeat every volume you want kept) | `from huggingface_hub import HfApi, Volume`<br>`HfApi().set_space_volumes("<namespace>/<repo>", volumes=[Volume(type="bucket", source="<org>/<bucket>", mount_path="/<mount_path>")])`<br>(same replace-not-append behavior — pass every volume you want kept) |
| **POST delete bucket from space** | `hf spaces volumes delete <namespace>/<repo>`<br>— removes **all** volumes (bucket, model, dataset, space mounts alike); there is no per-volume delete, only "set the full replacement list" or "delete everything" | `HfApi().delete_space_volumes("<namespace>/<repo>")`<br>— same all-or-nothing scope; to drop just the bucket while keeping other mounts, call `set_space_volumes(...)` with the bucket omitted from the list instead |
| **POST set variable** | `hf spaces variables add <namespace>/<repo> -e KEY=value`<br>(`--env-file <path>` to set many at once) | `HfApi().add_space_variable("<namespace>/<repo>", key="KEY", value="value")` |
| **POST set secret** | `hf spaces secrets add <namespace>/<repo> -s KEY=value`<br>(`--secrets-file <path>` to set many at once) | `HfApi().add_space_secret("<namespace>/<repo>", key="KEY", value="value")` |
| **POST set sleep time-out** | `hf spaces settings <namespace>/<repo> --sleep-time <seconds>`<br>(`-1` = never sleep; only available on upgraded/paid hardware, not `cpu-basic`) | `HfApi().set_space_sleep_time("<namespace>/<repo>", sleep_time=<seconds>)` |

## Notes

- All of the above accept an optional `token`/`--token`; both default to the
  locally saved/logged-in credential (`HF_TOKEN` in this project's
  environment) if omitted.
- **Pause vs. sleep are different states.** `pause_space` puts the Space in
  an explicit paused state (not billed, stays paused until you restart it
  manually) — distinct from the automatic "sleep" a free-tier Space enters
  after inactivity. `set_space_sleep_time(repo_id, seconds)` /
  `hf spaces settings <repo> --sleep-time <seconds>` configures that
  automatic sleep threshold (upgraded hardware only; `-1` disables it), it
  does not pause the Space itself.
- Per `docs/v0.2.0/hf-space-sleep-clears-cache.md`, both a sleep→wake cycle
  and an explicit pause→restart cycle mean a **full container rebuild from
  the Dockerfile**, not a resume — anything on that container's local disk
  (including the HF Hub download cache) is gone either way.
- **The `stage` values in this `huggingface_hub` 1.27.0** (`SpaceStage`
  enum, checked directly, not from memory): `NO_APP_FILE`, `CONFIG_ERROR`,
  `BUILDING`, `BUILD_ERROR`, `RUNNING`, `RUNNING_BUILDING`,
  `RUNTIME_ERROR`, `DELETING`, `STOPPED`, `PAUSED`, `APP_STARTING`,
  `RUNNING_APP_STARTING`. There is **no distinct "asleep"/"sleeping"
  value** — a Space that's gone idle and slept reports `STOPPED`, the same
  stage a crashed-and-stopped Space would show; it's `sleep_time` (see the
  sleep time-out row above) plus recent activity that tells you *why* it's
  stopped, not `stage` itself. `PAUSED` is reserved for an explicit
  `pause_space` call, not automatic sleep.
- `HfApi().request_space_hardware(repo_id, hardware, sleep_time=None)` is
  the corresponding call to *change* hardware tier (not listed above since
  it wasn't one of the requested actions) — `hf spaces settings <repo>
  --hardware <tier>` is its CLI equivalent.
