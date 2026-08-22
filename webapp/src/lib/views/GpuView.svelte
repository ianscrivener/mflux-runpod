<script>
  import DOMPurify from "dompurify";
  import { marked } from "marked";
  import { api } from "../api.js";
  import Modal from "../components/Modal.svelte";

  // ---------------------------------------------------------------------
  // Status track + pause/start/instance-size -- wired to GET/POST
  // /gpu/status|pause|start|hardware.
  // ---------------------------------------------------------------------

  const STATE_SEQUENCE = [
    { id: "sleeping", label: "Sleeping" },
    { id: "starting", label: "Starting" },
    { id: "idle", label: "Idle" },
    { id: "building", label: "Building" },
  ];

  // SpaceStage values (huggingface_hub) bucketed into the 4 pills above --
  // see docs/_HF_SPACE_COMMANDS.md. Error stages (CONFIG_ERROR etc) fall
  // into "sleeping" too, since Start (restart_space) is the same recovery
  // action for those as for a plain paused/asleep Space.
  const SLEEPING_STAGES = new Set([
    "PAUSED", "STOPPED", "NO_APP_FILE", "CONFIG_ERROR", "BUILD_ERROR", "RUNTIME_ERROR", "DELETING",
  ]);
  const STARTING_STAGES = new Set(["APP_STARTING", "BUILDING", "RUNNING_APP_STARTING", "RUNNING_BUILDING"]);

  // Instance-size selector's options come from singleGpuTiers (below, backed
  // by GET /gpu/hardware -- data-hf-sync/hf_hardware.json) rather than a
  // separate curated list, so it can never drift out of sync with the
  // pricing table shown in the GPU-pricing modal.
  let selectedHardware = $state(null);
  let confirmAction = $state(null); // "pause" | "start" | "hardware" | null
  let pauseStartBusy = $state(false);
  let pauseStartError = $state(null);
  let hardwareApplyBusy = $state(false);
  let hardwareApplyError = $state(null);

  // Seed the selector from the Space's actual current tier the first time
  // GET /gpu/status reports one -- only once (guarded by selectedHardware
  // still being null), so it doesn't stomp on a tier the user has already
  // picked but not applied yet.
  $effect(() => {
    if (selectedHardware === null && status?.hardware) {
      selectedHardware = status.hardware;
    }
  });

  // Changing hardware tier restarts the Space (request_space_hardware, see
  // docs/_HF_SPACE_COMMANDS.md) -- same build-interrupting consequence as
  // Pause, so Apply is blocked outright while a build is in progress, not
  // just gated behind the confirm dialog's wording.
  const canApplyHardware = $derived(
    !!selectedHardware &&
    selectedHardware !== status?.hardware &&
    status?.state !== "building" &&
    !hardwareApplyBusy
  );

  // status.stage comes from GET /gpu/status (app.generate.worker_status --
  // the Space's own SpaceStage) -- null until the first poll lands.
  const currentStateId = $derived.by(() => {
    const stage = status?.stage;
    if (!stage) return null;
    if (SLEEPING_STAGES.has(stage)) return "sleeping";
    if (STARTING_STAGES.has(stage)) return "starting";
    if (stage === "RUNNING") return status?.state === "building" ? "building" : "idle";
    return null;
  });

  const canStart = $derived(currentStateId === "sleeping");
  const canPause = $derived(currentStateId === "idle" || currentStateId === "building" || currentStateId === "starting");

  function requestConfirm(action) {
    confirmAction = action;
  }

  async function proceedConfirm() {
    const action = confirmAction;
    confirmAction = null;

    if (action === "hardware") {
      hardwareApplyBusy = true;
      hardwareApplyError = null;
      try {
        await api.gpuSetHardware(selectedHardware);
        await load();
      } catch (e) {
        hardwareApplyError = e.message;
      } finally {
        hardwareApplyBusy = false;
      }
      return;
    }

    pauseStartBusy = true;
    pauseStartError = null;
    try {
      if (action === "start") {
        await api.gpuStart();
      } else if (action === "pause") {
        await api.gpuPause();
      }
      await load();
    } catch (e) {
      pauseStartError = e.message;
    } finally {
      pauseStartBusy = false;
    }
  }

  // ---------------------------------------------------------------------
  // Live worker status -- GET /gpu/status. Only feeds the status-track pills
  // (currentStateId, above) now -- the Worker state/Queue depth/Prefetching/
  // Current job/Last result panels that used to render loading/error state
  // for this were removed, so a fetch failure just leaves no pill lit
  // rather than showing a separate message.
  // ---------------------------------------------------------------------

  let status = $state(null);

  async function load() {
    try {
      status = await api.gpuStatus();
    } catch {
      status = null;
    }
  }

  load();
  const poll = setInterval(load, 5000);
  $effect(() => () => clearInterval(poll));

  // ---------------------------------------------------------------------
  // Hardware tiers table -- GET /gpu/hardware (data-hf-sync/hf_hardware.json).
  // Shown in the GPU-pricing modal (opened from the control card), not
  // inline -- loaded once regardless of whether that modal's been opened
  // yet, so it's ready the first time it is.
  // ---------------------------------------------------------------------

  let pricingOpen = $state(false);
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

  // ---------------------------------------------------------------------
  // Model card template preview -- GET /models_hf/card_preview. Not GPU-
  // related, just parked here for now (design/review stage) until it has a
  // proper home.
  // ---------------------------------------------------------------------

  let cardPreviewOpen = $state(false);
  let cardMarkdown = $state("");
  let cardError = $state(null);
  let cardLoading = $state(false);
  let cardView = $state("markdown"); // "markdown" | "html"

  // Rendered via {@html} below -- sanitized even though cardMarkdown's
  // source fields (model/repo names, MFlux CLI names) come from this
  // project's own configs/models/*.yaml and HF org scan, not arbitrary
  // user input; defense-in-depth for a value that reaches {@html} at all.
  const cardHtml = $derived(cardMarkdown ? DOMPurify.sanitize(marked.parse(cardMarkdown)) : "");

  async function openCardPreview() {
    cardPreviewOpen = true;
    cardView = "markdown";
    cardError = null;
    cardLoading = true;
    try {
      const res = await api.modelCardPreview();
      cardMarkdown = res.markdown;
    } catch (e) {
      cardError = e.message;
    } finally {
      cardLoading = false;
    }
  }

  // ---------------------------------------------------------------------
  // Worker logs -- GET /gpu/logs/build and /gpu/logs/container. Loaded once
  // on mount, each with its own refresh button (not polled -- these are
  // pulled straight from the HF Space's own log buffer on every request,
  // no reason to hit that repeatedly on a timer).
  // ---------------------------------------------------------------------

  let buildLogLines = $state([]);
  let buildLogError = $state(null);
  let buildLogLoading = $state(true);

  let containerLogLines = $state([]);
  let containerLogError = $state(null);
  let containerLogLoading = $state(true);

  async function loadBuildLogs() {
    buildLogLoading = true;
    buildLogError = null;
    try {
      const res = await api.gpuLogsBuild();
      buildLogLines = res.lines ?? [];
    } catch (e) {
      buildLogError = e.message;
    } finally {
      buildLogLoading = false;
    }
  }

  async function loadContainerLogs() {
    containerLogLoading = true;
    containerLogError = null;
    try {
      const res = await api.gpuLogsContainer();
      containerLogLines = res.lines ?? [];
    } catch (e) {
      containerLogError = e.message;
    } finally {
      containerLogLoading = false;
    }
  }

  loadBuildLogs();
  loadContainerLogs();
</script>

<h2 style="margin-bottom:16px">GPU</h2>

<div class="gpu-split">
  <div class="col-left">
    <div class="status-track card">
      {#each STATE_SEQUENCE as s (s.id)}
        <div class="status-step" class:current={s.id === currentStateId}>
          <span class="status-dot"></span>
          <span class="status-name">{s.label}</span>
        </div>
      {/each}
    </div>

    <p style="margin-top:16px">
      <button type="button" class="link-button" onclick={openCardPreview}>Preview HF model card template →</button>
    </p>
  </div>

  <div class="col-right">
    <div class="card control-card">
      <div class="control-row">
        <div>
          <div class="gpu-card-label">Instance size</div>
          <div class="gpu-card-value">
            {singleGpuTiers.find((t) => t.name === selectedHardware)?.["pretty name"] ?? selectedHardware ?? "—"}
          </div>
        </div>
        <div class="hardware-controls">
          <select bind:value={selectedHardware} aria-label="Instance size">
            {#each singleGpuTiers as t (t.name)}<option value={t.name}>{t["pretty name"]}</option>{/each}
          </select>
          <button disabled={!canApplyHardware} onclick={() => requestConfirm("hardware")}>Apply</button>
        </div>
      </div>

      <div class="control-row buttons-row">
        <div class="buttons-left">
          <button class="danger" disabled={!canPause || pauseStartBusy} onclick={() => requestConfirm("pause")}>Pause</button>
          <button class="primary" disabled={!canStart || pauseStartBusy} onclick={() => requestConfirm("start")}>Start</button>
        </div>
        <button type="button" class="pill accent pricing-pill" onclick={() => (pricingOpen = true)}>GPU pricing</button>
      </div>

      {#if pauseStartError}
        <p class="pill danger">{pauseStartError}</p>
      {/if}
      {#if hardwareApplyError}
        <p class="pill danger">{hardwareApplyError}</p>
      {/if}
    </div>
  </div>
</div>

{#if pricingOpen}
  <Modal wide onclose={() => (pricingOpen = false)}>
    <h2 style="margin-bottom:12px">HF Spaces hardware tiers</h2>
    {#if hardwareError}
      <p class="pill danger">{hardwareError}</p>
    {:else if singleGpuTiers.length}
      <table class="hw-table">
        <thead>
          <tr><th>Name</th><th>Tier</th><th>CPU</th><th>RAM</th><th>GPU</th><th>VRAM</th><th>Cost/min</th><th>Cost/hour</th></tr>
        </thead>
        <tbody>
          {#each singleGpuTiers as t (t.name)}
            <tr class:current-tier={t.name === status?.hardware}>
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
      <p class="faint">Loading…</p>
    {/if}
  </Modal>
{/if}

{#if confirmAction === "pause"}
  <Modal onclose={() => (confirmAction = null)}>
    <h2 style="margin-bottom:8px">Pause the GPU worker?</h2>
    <p>
      This explicitly pauses the Hugging Face Space (not billed while paused). Any in-progress
      build will be interrupted, and the Space's local disk cache (downloaded source weights)
      will be cleared on the next Start -- see <code>docs/v0.2.0/hf-space-sleep-clears-cache.md</code>.
      Only Start (restart) brings a paused Space back.
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
{:else if confirmAction === "hardware"}
  <Modal onclose={() => (confirmAction = null)}>
    <h2 style="margin-bottom:8px">Change instance size?</h2>
    <p>
      This restarts the Space on <strong>{singleGpuTiers.find((t) => t.name === selectedHardware)?.["pretty name"] ?? selectedHardware}</strong>.
      Any in-progress build will be interrupted, and the Space's local disk cache (downloaded
      source weights) will be cleared -- see <code>docs/v0.2.0/hf-space-sleep-clears-cache.md</code>.
    </p>
    <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:16px">
      <button onclick={() => (confirmAction = null)}>Cancel</button>
      <button class="primary" onclick={proceedConfirm}>Apply</button>
    </div>
  </Modal>
{/if}

<div class="logs-split">
  <div class="col-left">
    <div class="card logs-card">
      <div class="logs-header">
        <h3>Build logs</h3>
        <button type="button" class="link-button" disabled={buildLogLoading} onclick={loadBuildLogs}>
          {buildLogLoading ? "Loading…" : "Refresh"}
        </button>
      </div>
      <div class="log-window">
        {#if buildLogLoading && !buildLogLines.length}
          <p class="muted">Loading…</p>
        {:else if buildLogError}
          <p class="pill danger">{buildLogError}</p>
        {:else if buildLogLines.length}
          <pre class="log-lines">{buildLogLines.join("\n")}</pre>
        {:else}
          <p class="faint">No build log lines.</p>
        {/if}
      </div>
    </div>
  </div>

  <div class="col-right">
    <div class="card logs-card">
      <div class="logs-header">
        <h3>Container logs</h3>
        <button type="button" class="link-button" disabled={containerLogLoading} onclick={loadContainerLogs}>
          {containerLogLoading ? "Loading…" : "Refresh"}
        </button>
      </div>
      <div class="log-window">
        {#if containerLogLoading && !containerLogLines.length}
          <p class="muted">Loading…</p>
        {:else if containerLogError}
          <p class="pill danger">{containerLogError}</p>
        {:else if containerLogLines.length}
          <pre class="log-lines">{containerLogLines.join("\n")}</pre>
        {:else}
          <p class="faint">No container log lines.</p>
        {/if}
      </div>
    </div>
  </div>
</div>

{#if cardPreviewOpen}
  <Modal wide onclose={() => (cardPreviewOpen = false)}>
    <h2 style="margin-bottom:12px">HF model card template preview</h2>

    <div class="view-toggle">
      <button type="button" class="pill" class:accent={cardView === "markdown"} onclick={() => (cardView = "markdown")}>
        Markdown
      </button>
      <button type="button" class="pill" class:accent={cardView === "html"} onclick={() => (cardView = "html")}>
        HTML
      </button>
    </div>

    {#if cardLoading}
      <p class="muted">Loading…</p>
    {:else if cardError}
      <p class="pill danger">{cardError}</p>
    {:else if cardView === "markdown"}
      <pre class="card-markdown">{cardMarkdown}</pre>
    {:else}
      <div class="card-html">{@html cardHtml}</div>
    {/if}
  </Modal>
{/if}

<style>
  .logs-split {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    margin-top: 32px;
  }

  .logs-card {
    display: flex;
    flex-direction: column;
    padding: 16px;
    min-width: 0;
  }

  .logs-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }

  .logs-header h3 {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
  }

  .logs-header .link-button:disabled {
    color: var(--muted);
    cursor: wait;
  }

  .log-window {
    height: clamp(300px, 60vh, 800px);
    /* height: min(300px, 20vh);
    height: max(600px, 40vh); */
    overflow-y: auto;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
  }

  .log-lines {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 12px;
    line-height: 1.5;
  }

  .link-button {
    background: none;
    border: none;
    padding: 0;
    color: var(--accent);
    font-family: "IBM Plex Sans", sans-serif;
    font-size: 13px;
    cursor: pointer;
  }

  .link-button:hover {
    text-decoration: underline;
  }

  .card-markdown {
    white-space: pre-wrap;
    word-break: break-word;
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 12px;
    line-height: 1.5;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    margin: 0;
  }

  .view-toggle {
    display: flex;
    gap: 6px;
    margin-bottom: 12px;
  }

  .view-toggle .pill {
    cursor: pointer;
  }

  .card-html {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    font-size: 13px;
    line-height: 1.6;
    overflow-x: auto;
  }

  .card-html :global(table) {
    border-collapse: collapse;
    margin: 12px 0;
  }

  .card-html :global(td),
  .card-html :global(th) {
    border: 1px solid var(--border);
    padding: 4px 10px;
    white-space: nowrap;
  }

  /* marked() emits the deprecated align="center" attribute for markdown's
     :--: column syntax -- app.css's global `td { text-align: left; }` beats
     that presentational attribute on specificity, so it has to be overridden
     explicitly here rather than relying on align= alone. */
  .card-html :global(td[align="center"]),
  .card-html :global(th[align="center"]) {
    text-align: center;
  }

  .card-html :global(a) {
    color: var(--accent);
  }

  .card-html :global(pre) {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    overflow-x: auto;
  }

  .card-html :global(code) {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-size: 12px;
  }

  .gpu-split {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    align-items: start;
    margin-bottom: 16px;
  }

  .col-left,
  .col-right {
    min-width: 0;
  }

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

  .hardware-controls {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .buttons-left {
    display: flex;
    gap: 10px;
  }

  .buttons-left button {
    min-width: 100px;
  }

  .pricing-pill {
    cursor: pointer;
  }

  .current-tier td {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
  }

  .current-tier td:first-child {
    box-shadow: inset 3px 0 var(--accent);
  }

  .hw-table {
    font-size: 80%;
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
