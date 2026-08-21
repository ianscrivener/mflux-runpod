<script>
  import { api } from "../api.js";
  import Modal from "../components/Modal.svelte";

  // ---------------------------------------------------------------------
  // Design-only controls (status track, instance size, pause/start).
  // Deliberately NOT wired to any backend yet -- mockState/mockHardware are
  // local UI state only, so this section can be reviewed and iterated on
  // before any /gpu/hardware or /gpu/pause|start endpoint exists. Wiring
  // notes are left inline where a real call will eventually go.
  // ---------------------------------------------------------------------

  const STATE_SEQUENCE = [
    { id: "sleeping", label: "Sleeping" },
    { id: "starting", label: "Starting" },
    { id: "idle", label: "Idle" },
    { id: "building", label: "Building" },
  ];

  // Real hardware tier ids from HF Spaces (huggingface_hub.SpaceHardware) --
  // this list will eventually come from list_spaces_hardware(), see
  // docs/_HF_SPACE_COMMANDS.md.
  const HARDWARE_TIERS = [
    { id: "cpu-basic", label: "CPU basic (free)" },
    { id: "t4-small", label: "T4 small" },
    { id: "t4-medium", label: "T4 medium" },
    { id: "l4x1", label: "L4 x1" },
    { id: "a10g-small", label: "A10G small" },
    { id: "a10g-large", label: "A10G large" },
    { id: "a100-large", label: "A100 large" },
  ];

  let mockState = $state("sleeping");
  let mockHardware = $state("a10g-large");
  let confirmAction = $state(null); // "pause" | "start" | null

  const canStart = $derived(mockState === "sleeping");
  const canPause = $derived(mockState === "idle" || mockState === "building");

  function requestConfirm(action) {
    confirmAction = action;
  }

  function proceedConfirm() {
    // TODO(backend): call POST /gpu/pause or /gpu/start (pause_space /
    // restart_space, see docs/_HF_SPACE_COMMANDS.md) and reconcile mockState
    // from the real response instead of simulating it locally.
    if (confirmAction === "start") {
      mockState = "starting";
      setTimeout(() => { mockState = "idle"; }, 1500);
    } else if (confirmAction === "pause") {
      mockState = "sleeping";
    }
    confirmAction = null;
  }

  // ---------------------------------------------------------------------
  // Live worker status -- already wired to GET /gpu/status.
  // ---------------------------------------------------------------------

  let status = $state(null);
  let loading = $state(true);
  let error = $state(null);
  let expected = $state(false);

  async function load() {
    try {
      status = await api.gpuStatus();
      error = null;
      expected = false;
    } catch (e) {
      // /gpu/status: 503 = HF_WORKER_URL not set on this deployment, 502 =
      // configured but the worker didn't respond -- most commonly because
      // its HF Space is asleep (it sleeps on idle and wakes on the next
      // request, see docs/v0.2.0/hf-space-sleep-clears-cache.md) or still
      // restarting. Both are expected/normal, not bugs, so shown as a
      // status message rather than the red error pill.
      expected = e.message.startsWith("503 ") || e.message.startsWith("502 ");
      error = e.message;
      status = null;
    } finally {
      loading = false;
    }
  }

  load();
  const poll = setInterval(load, 5000);
  $effect(() => () => clearInterval(poll));

  const stateLabel = $derived(status?.state === "building" ? "Building" : status?.state === "idle" ? "Idle" : status?.state ?? "—");

  // ---------------------------------------------------------------------
  // Hardware tiers table -- GET /gpu/hardware (data-hf-sync/hf_hardware.json).
  // Static specs/pricing, not part of the live-status poll above -- loaded once.
  // ---------------------------------------------------------------------

  let hardwareTiers = $state([]);
  let hardwareError = $state(null);

  async function loadHardware() {
    try {
      const res = await api.gpuHardware();
      hardwareTiers = res.tiers ?? [];
    } catch (e) {
      hardwareError = e.message;
    }
  }

  loadHardware();

  // Multi-GPU tiers (name ends "x2"/"x4"/"x8", e.g. a10g-largex4, a100x8) --
  // this worker only ever runs one build at a time (see worker.py), so more
  // than 1 GPU on a tier buys nothing here; hidden to keep the table short.
  // "accelerator" splits "1x A10G (24 GB)" -> gpu "A10G", vram "24 GB"; null
  // (cpu-basic, zero-a10g) -> both "—".
  const singleGpuTiers = $derived(
    hardwareTiers
      .filter((t) => !/x[2-9]\d*$/.test(t.name))
      .map((t) => {
        const m = t.accelerator?.match(/^\d+x\s*(.+?)\s*\(([^)]+)\)$/);
        return { ...t, gpu: m ? m[1] : "—", vram: m ? m[2] : "—" };
      })
  );
</script>

<h2 style="margin-bottom:16px">GPU</h2>

<div class="status-track card">
  {#each STATE_SEQUENCE as s (s.id)}
    <div class="status-step" class:current={s.id === mockState}>
      <span class="status-dot"></span>
      <span class="status-name">{s.label}</span>
    </div>
  {/each}
</div>

<div class="card control-card">
  <div class="control-row">
    <div>
      <div class="gpu-card-label">Instance size</div>
      <div class="gpu-card-value">{HARDWARE_TIERS.find((h) => h.id === mockHardware)?.label}</div>
    </div>
    <select bind:value={mockHardware}>
      {#each HARDWARE_TIERS as h (h.id)}<option value={h.id}>{h.label}</option>{/each}
    </select>
  </div>

  <div class="control-row buttons-row">
    <button class="danger" disabled={!canPause} onclick={() => requestConfirm("pause")}>Pause</button>
    <button class="primary" disabled={!canStart} onclick={() => requestConfirm("start")}>Start</button>
  </div>
</div>

<p class="muted" style="font-size:11px; margin: 6px 0 20px">
  Design preview — the status track, instance size, and pause/start controls above are not
  wired to the backend yet.
</p>

{#if confirmAction === "pause"}
  <Modal onclose={() => (confirmAction = null)}>
    <h2 style="margin-bottom:8px">Pause the GPU worker?</h2>
    <p>
      This puts the Hugging Face Space to sleep. Any in-progress build will be interrupted, and
      the Space's local disk cache (downloaded source weights) will be cleared on next wake --
      see <code>docs/v0.2.0/hf-space-sleep-clears-cache.md</code>.
    </p>
    <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:16px">
      <button onclick={() => (confirmAction = null)}>Cancel</button>
      <button class="danger" onclick={proceedConfirm}>Pause</button>
    </div>
  </Modal>
{:else if confirmAction === "start"}
  <Modal onclose={() => (confirmAction = null)}>
    <h2 style="margin-bottom:8px">Start the GPU worker?</h2>
    <p>
      This wakes the Hugging Face Space from sleep. It can take a minute or two to become ready
      to accept builds.
    </p>
    <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:16px">
      <button onclick={() => (confirmAction = null)}>Cancel</button>
      <button class="primary" onclick={proceedConfirm}>Start</button>
    </div>
  </Modal>
{/if}

{#if loading}
  <p class="muted">Loading…</p>
{:else if expected}
  <p class="pill warn">{error}</p>
{:else if error}
  <p class="pill danger">{error}</p>
{:else if status}
  <div class="gpu-grid">
    <div class="card gpu-card">
      <div class="gpu-card-label">Worker state</div>
      <div class="gpu-card-value">
        <span class="pill" class:success={status.state === "building"} class:accent={status.state === "idle"}>
          {stateLabel}
        </span>
      </div>
    </div>

    <div class="card gpu-card">
      <div class="gpu-card-label">Queue depth</div>
      <div class="gpu-card-value"><span class="hero-value">{status.queue_depth ?? 0}</span></div>
    </div>

    <div class="card gpu-card">
      <div class="gpu-card-label">Prefetching</div>
      <div class="gpu-card-value">
        {#if status.prefetching?.length}
          {#each status.prefetching as name}<span class="pill accent" style="margin:0 4px 4px 0">{name}</span>{/each}
        {:else}
          <span class="faint">none</span>
        {/if}
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:16px; padding:16px">
    <h3 style="font-size:13px; text-transform:uppercase; letter-spacing:0.04em; color:var(--muted); margin-bottom:10px">
      Current job
    </h3>
    {#if status.current}
      <table>
        <tbody>
          <tr><td class="muted">Model</td><td>{status.current.config_stem ?? "—"}</td></tr>
          <tr><td class="muted">Quant</td><td>{status.current.quant ?? "—"}</td></tr>
          <tr><td class="muted">Run</td><td>{status.current.run_id ?? "—"}</td></tr>
        </tbody>
      </table>
    {:else}
      <p class="faint" style="margin:0">No build in progress.</p>
    {/if}
  </div>

  <div class="card" style="margin-top:16px; padding:16px">
    <h3 style="font-size:13px; text-transform:uppercase; letter-spacing:0.04em; color:var(--muted); margin-bottom:10px">
      Last result
    </h3>
    {#if status.last_result}
      <table>
        <tbody>
          {#each Object.entries(status.last_result) as [k, v]}
            <tr><td class="muted">{k}</td><td>{v === null || v === undefined ? "—" : String(v)}</td></tr>
          {/each}
        </tbody>
      </table>
    {:else}
      <p class="faint" style="margin:0">No completed builds yet this session.</p>
    {/if}
  </div>
{/if}

<div class="card scroll-x hw-card" style="margin-top:16px">
  <h3 style="font-size:13px; text-transform:uppercase; letter-spacing:0.04em; color:var(--muted); padding:16px 16px 0">
    HF Spaces hardware tiers
  </h3>
  {#if hardwareError}
    <p class="pill danger" style="margin:16px">{hardwareError}</p>
  {:else if singleGpuTiers.length}
    <table class="hw-table">
      <thead>
        <tr><th>Name</th><th>Tier</th><th>CPU</th><th>RAM</th><th>GPU</th><th>VRAM</th><th>Cost/min</th><th>Cost/hour</th></tr>
      </thead>
      <tbody>
        {#each singleGpuTiers as t (t.name)}
          <tr class:current-tier={t.name === mockHardware}>
            <td class="mono">{t.name}</td>
            <td>{t["pretty name"]}</td>
            <td>{t.cpu}</td>
            <td>{t.ram}</td>
            <td>{t.gpu}</td>
            <td>{t.vram}</td>
            <td>{t["cost/min"]}</td>
            <td>{t["cost/hour"]}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {:else}
    <p class="faint" style="margin:16px">Loading…</p>
  {/if}
</div>

<style>
  .status-track {
    display: flex;
    gap: 8px;
    padding: 16px;
    margin-bottom: 16px;
  }

  .status-step {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    padding: 10px 14px;
    border-radius: 8px;
    background: var(--surface-2);
    border: 1px solid var(--border);
  }

  .status-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--faint);
    flex-shrink: 0;
  }

  .status-name {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 13px;
    color: var(--muted);
  }

  .status-step.current {
    background: color-mix(in srgb, var(--success) 16%, var(--surface));
    border-color: var(--success);
  }

  .status-step.current .status-dot {
    background: var(--success);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 25%, transparent);
  }

  .status-step.current .status-name {
    color: var(--success);
    font-weight: 600;
  }

  .control-card {
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .control-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }

  .control-row select {
    min-width: 200px;
  }

  .buttons-row {
    justify-content: flex-start;
    gap: 10px;
  }

  .buttons-row button {
    min-width: 100px;
  }

  .current-tier td {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
  }

  .current-tier td:first-child {
    box-shadow: inset 3px 0 var(--accent);
  }

  .hw-card {
    max-width: 640px;
    width: fit-content;
  }

  .hw-table {
    font-size: 80%;
  }

  .gpu-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 14px;
  }

  .gpu-card {
    padding: 16px;
  }

  .gpu-card-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
    margin-bottom: 8px;
  }

  .gpu-card-value {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
  }

  .hero-value {
    font-size: 28px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  table:not(.hw-table) td {
    white-space: normal;
    word-break: break-word;
  }

  table:not(.hw-table) td:first-child {
    width: 160px;
  }

  .hw-table td,
  .hw-table th {
    white-space: nowrap;
  }
</style>
