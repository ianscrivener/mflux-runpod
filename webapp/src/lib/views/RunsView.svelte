<script>
  import { api } from "../api.js";
  import StatusPill from "../components/StatusPill.svelte";

  let runs = $state([]);
  let stemOptions = $state([]);
  let loading = $state(true);
  let error = $state(null);
  let expanded = $state({}); // run id -> bool
  let details = $state({}); // run id -> detail
  let busy = $state({});

  let configStem = $state("");
  let quants = $state("");
  let mfluxRepo = $state("");
  let mfluxBranch = $state("");
  let forceOverwrite = $state(false);
  let dispatch = $state(false);
  let generating = $state(false);
  let generateMsg = $state(null);

  async function load() {
    try {
      const [reportRes, missingRes] = await Promise.all([api.report({ limit: 30 }), api.modelsMissing()]);
      runs = reportRes.runs;
      stemOptions = [...Object.keys(missingRes.missing), ...missingRes.complete].sort();
      if (!configStem && stemOptions.length) configStem = stemOptions[0];
      error = null;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  load();
  const poll = setInterval(load, 6000);
  $effect(() => () => clearInterval(poll));

  async function toggle(run) {
    expanded = { ...expanded, [run.id]: !expanded[run.id] };
    if (expanded[run.id] && !details[run.id]) {
      try {
        details = { ...details, [run.id]: await api.report({ run_id: run.id }) };
      } catch (e) {
        // Populate details even on failure -- {#if !details[run.id]} below
        // is what shows "Loading…", so leaving it unset on a rejected fetch
        // means the row stays stuck there forever (only a second toggle,
        // discovered by accident, retries the fetch). quant_builds: [] so
        // the existing quant-builds table render doesn't crash on it.
        details = { ...details, [run.id]: { quant_builds: [], error: e.message } };
      }
    }
  }

  async function cancel(run) {
    if (!confirm(`Cancel run #${run.id} (${run.model_series})?`)) return;
    busy = { ...busy, [run.id]: true };
    try {
      await api.generateCancel(run.id);
      await load();
    } catch (e) {
      error = e.message;
    } finally {
      busy = { ...busy, [run.id]: false };
    }
  }

  function parseQuants(text) {
    const trimmed = text.trim();
    if (!trimmed) return null;
    return trimmed.split(",").map((s) => s.trim()).filter(Boolean);
  }

  async function submitGenerate() {
    if (!configStem) return;
    generating = true;
    generateMsg = null;
    try {
      const res = await api.generate({
        config_stem: configStem,
        quants: parseQuants(quants),
        mflux_repo: mfluxRepo || null,
        mflux_branch: mfluxBranch || null,
        force_hf_overwrite: forceOverwrite,
        dispatch,
      });
      generateMsg = `run #${res.run_id ?? res.id ?? "?"} started (${dispatch ? "dispatched" : "dry-run"})`;
      await load();
    } catch (e) {
      generateMsg = e.message;
    } finally {
      generating = false;
    }
  }

  async function clearLog() {
    if (!confirm("Clear the entire generation log (runs + quant_builds)? This is irreversible.")) return;
    try {
      await api.reportClear();
      await load();
    } catch (e) {
      error = e.message;
    }
  }
</script>

<h2>Generate</h2>
<div class="card gen-form">
  <select bind:value={configStem}>
    {#each stemOptions as s}<option value={s}>{s}</option>{/each}
  </select>
  <input placeholder="quants (blank = all missing)" bind:value={quants} />
  <input placeholder="mflux repo override" bind:value={mfluxRepo} />
  <input placeholder="branch" bind:value={mfluxBranch} style="width:100px" />
  <label class="check-inline"><input type="checkbox" bind:checked={forceOverwrite} /> force overwrite</label>
  <label class="check-inline"><input type="checkbox" bind:checked={dispatch} /> dispatch (real, billed)</label>
  <button class="primary" onclick={submitGenerate} disabled={generating || !configStem}>
    {generating ? "…" : dispatch ? "Dispatch" : "Dry run"}
  </button>
</div>
{#if generateMsg}<p class="muted" style="font-size:12px">{generateMsg}</p>{/if}

<div class="header-row">
  <h2>Runs</h2>
  <button class="danger" onclick={clearLog}>Clear log</button>
</div>

{#if error}
  <p class="pill danger">{error}</p>
{:else if loading}
  <p class="muted">Loading…</p>
{:else}
  <div class="card scroll-x">
    <table>
      <thead>
        <tr><th></th><th>#</th><th>Series</th><th>Status</th><th>Started</th><th>Duration</th><th>Quants</th><th></th></tr>
      </thead>
      <tbody>
        {#each runs as run (run.id)}
          <tr>
            <td>
              <button
                type="button"
                class="row-toggle"
                onclick={() => toggle(run)}
                aria-expanded={!!expanded[run.id]}
                aria-label="Toggle details for run {run.id}"
              >{expanded[run.id] ? "▾" : "▸"}</button>
            </td>
            <td class="muted">{run.id}</td>
            <td><strong>{run.model_series}</strong></td>
            <td><StatusPill status={run.status} /></td>
            <td class="muted" style="font-size:11px">{run.started_at?.slice(0, 19).replace("T", " ")}</td>
            <td class="muted">{run.duration_s ? `${run.duration_s.toFixed(1)}s` : "—"}</td>
            <td class="muted">{run.expected_quants}</td>
            <td>
              {#if run.status === "running"}
                <button class="danger" disabled={busy[run.id]} onclick={() => cancel(run)}>
                  Cancel
                </button>
              {/if}
            </td>
          </tr>
          {#if expanded[run.id]}
            <tr>
              <td colspan="8">
                {#if !details[run.id]}
                  <span class="muted">Loading…</span>
                {:else}
                  <table class="nested">
                    <thead>
                      <tr><th>Quant</th><th>Status</th><th>Size</th><th>Build</th><th>Upload</th><th>Repo</th></tr>
                    </thead>
                    <tbody>
                      {#each details[run.id].quant_builds as qb}
                        <tr>
                          <td>{qb.quant}</td>
                          <td><StatusPill status={qb.status} /></td>
                          <td class="muted">{qb.total_size_bytes ? `${(qb.total_size_bytes / 1e9).toFixed(2)} GB` : "—"}</td>
                          <td class="muted">{qb.build_duration_s ? `${qb.build_duration_s.toFixed(0)}s` : "—"}</td>
                          <td class="muted">{qb.upload_duration_s ? `${qb.upload_duration_s.toFixed(0)}s` : "—"}</td>
                          <td class="muted">{qb.hf_repo_id ?? "—"}</td>
                        </tr>
                      {/each}
                      {#if details[run.id].quant_builds.length === 0}
                        <tr><td colspan="6" class="muted">No quant builds reported yet.</td></tr>
                      {/if}
                    </tbody>
                  </table>
                  {#if details[run.id].error}
                    <p class="pill danger" style="margin-top:8px">{details[run.id].error}</p>
                  {/if}
                {/if}
              </td>
            </tr>
          {/if}
        {/each}
        {#if runs.length === 0}
          <tr><td colspan="8" class="muted">No runs yet.</td></tr>
        {/if}
      </tbody>
    </table>
  </div>
{/if}

<style>
  .header-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin: 24px 0 12px;
  }
  .gen-form {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 12px;
    margin: 12px 0;
    flex-wrap: wrap;
  }
  .check-inline {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }
  .row-toggle {
    all: unset;
    cursor: pointer;
    font: inherit;
    color: inherit;
    padding: 2px 6px;
  }
  .row-toggle:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  table.nested {
    margin: 4px 0;
  }
  table.nested th,
  table.nested td {
    padding: 4px 8px;
    font-size: 12px;
  }
</style>
