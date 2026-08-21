<script>
  import { api } from "../api.js";
  import StatusPill from "../components/StatusPill.svelte";
  import Modal from "../components/Modal.svelte";

  let runs = $state([]);
  let stemOptions = $state([]);
  let missingData = $state({ missing: {}, complete: [] }); // from /models_missing, reused for the pre-generate already-published check
  let loading = $state(true);
  let error = $state(null);
  let expanded = $state({}); // run id -> bool
  let details = $state({}); // run id -> detail
  let buildsByRun = $state({}); // run id -> quant_builds[], for the collapsed row's per-quant coloring
  let busy = $state({});

  const QUANT_OPTIONS = ["bf16", "q8", "q6", "q5", "q4", "q3"];
  // Both mean "confirmed present on Hugging Face" -- skipped_existing is a
  // quant the backend found already published and left alone, uploaded is
  // one this run just pushed. Either way there's a real repo to link to.
  const DONE_QUANT_STATUSES = new Set(["uploaded", "skipped_existing"]);

  let configStem = $state("");
  let checkedQuants = $state({});
  let forceOverwrite = $state(false);
  let dryRun = $state(true);
  let generating = $state(false);
  let generateMsg = $state(null);
  let alreadyPublishedWarning = $state(null); // list of quants, or null if no warning showing

  async function load() {
    try {
      const [reportRes, missingRes, dumpRes] = await Promise.all([
        api.report({ limit: 30 }),
        api.modelsMissing(),
        api.reportDump(),
      ]);
      runs = reportRes.runs;
      missingData = missingRes;
      buildsByRun = Object.fromEntries((dumpRes.runs ?? []).map((r) => [r.id, r.quant_builds ?? []]));
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

  async function deleteRun(run) {
    if (!confirm(`Delete run #${run.id} (${run.model_series}) from the log? This does not affect any Hugging Face upload, and cannot be undone.`)) return;
    busy = { ...busy, [run.id]: true };
    try {
      await api.reportDeleteRun(run.id);
      await load();
    } catch (e) {
      error = e.message;
    } finally {
      busy = { ...busy, [run.id]: false };
    }
  }

  function selectedQuants() {
    const picked = QUANT_OPTIONS.filter((q) => checkedQuants[q]);
    return picked.length ? picked : null; // none checked = all missing
  }

  // Of the explicitly-checked quants, which already have a published repo
  // on Hugging Face -- so dispatching for them (with overwrite off) would
  // just spend real GPU time/money on a build the backend already knows
  // will get skipped at the upload step. Only meaningful for an explicit
  // selection; "none checked" already means "whatever's missing" and can't
  // pick something already-published by construction.
  function alreadyPublishedQuants(stem, picked) {
    if (!picked) return [];
    if (missingData.complete.includes(stem)) return picked;
    const missingSet = new Set(missingData.missing[stem]?.missing_quants ?? []);
    return picked.filter((q) => !missingSet.has(q));
  }

  function onGenerateClick() {
    if (!configStem) return;
    if (!forceOverwrite) {
      const already = alreadyPublishedQuants(configStem, selectedQuants());
      if (already.length) {
        alreadyPublishedWarning = already;
        return;
      }
    }
    submitGenerate();
  }

  async function submitGenerate() {
    alreadyPublishedWarning = null;
    generating = true;
    generateMsg = null;
    try {
      const res = await api.generate({
        config_stem: configStem,
        quants: selectedQuants(),
        force_hf_overwrite: forceOverwrite,
        dispatch: !dryRun,
      });
      generateMsg = `run #${res.run_id ?? res.id ?? "?"} started (${dryRun ? "dry-run" : "dispatched"})`;
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
  <div class="quant-checks">
    {#each QUANT_OPTIONS as q}
      <label class="check-inline">
        <input type="checkbox" checked={!!checkedQuants[q]} onchange={(e) => (checkedQuants = { ...checkedQuants, [q]: e.target.checked })} />
        {q.toUpperCase()}
      </label>
    {/each}
  </div>
  &nbsp; <b>|</b> &nbsp;
  <label class="check-inline"><input type="checkbox" bind:checked={forceOverwrite} /> overwrite</label>
  <label class="check-inline"><input type="checkbox" bind:checked={dryRun} /> dry run</label>
  <button class="primary" onclick={onGenerateClick} disabled={generating || !configStem}>
    {generating ? "…" : dryRun ? "Dry run" : "Dispatch"}
  </button>
</div>
<p class="muted" style="font-size:12px">
  <strong>force</strong>: force overwrite of Hugging Face MFlux-Community model &nbsp;|&nbsp; <strong>dry run</strong>: uncheck to convert model &ndash; incurs $$ billing
</p>
{#if generateMsg}<p class="muted" style="font-size:12px">{generateMsg}</p>{/if}

{#if alreadyPublishedWarning}
  {@const allExcluded = selectedQuants()?.length === alreadyPublishedWarning.length}
  <Modal onclose={() => (alreadyPublishedWarning = null)}>
    <h2 style="margin-bottom:8px">Already on Hugging Face</h2>
    <p>
      <strong>{configStem}</strong> already has a published repo for:
      <strong>{alreadyPublishedWarning.join(", ")}</strong>.
    </p>
    <p class="muted" style="font-size:12px">
      With "overwrite" unchecked, the service excludes these quant(s) before
      dispatch -- they're never sent to the worker at all, not built and
      then skipped at upload.
      {#if allExcluded}
        Every quant you selected is already published, so proceeding now
        would create a run with nothing left to build.
      {:else}
        Proceeding now will dispatch a run for only the remaining,
        not-yet-published quant(s).
      {/if}
      Check "overwrite" first if you actually want to rebuild {alreadyPublishedWarning.length > 1 ? "these" : "it"}.
    </p>
    <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:16px">
      <button onclick={() => (alreadyPublishedWarning = null)}>Cancel</button>
      <button class="primary" onclick={submitGenerate}>
        {allExcluded ? "Create empty run anyway" : "Build remaining quants"}
      </button>
    </div>
  </Modal>
{/if}

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
        <tr><th></th><th>#</th><th>Series</th><th>Quants</th><th>Status</th><th>Started</th><th>Duration</th><th>Quants Count</th><th></th></tr>
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
            <td class="muted">
              {#if run.quants?.length}
                {#each run.quants as q, i}
                  {@const qb = (buildsByRun[run.id] ?? []).find((b) => b.quant === q)}
                  {i > 0 ? " " : ""}{#if qb && DONE_QUANT_STATUSES.has(qb.status) && qb.hf_repo_id}<a
                      class="quant-done-link"
                      href="https://huggingface.co/{qb.hf_repo_id}"
                      target="_blank"
                      rel="noreferrer"
                    >{q}</a>{:else}{q}{/if}
                {/each}
              {:else}
                —
              {/if}
            </td>
            <td><StatusPill status={run.status} /></td>
            <td class="muted" style="font-size:11px">{run.started_at?.slice(0, 19).replace("T", " ")}</td>
            <td class="muted">{run.duration_s ? `${run.duration_s.toFixed(1)}s` : "—"}</td>
            <td class="muted">{run.expected_quants}</td>
            <td style="display:flex; gap:4px">
              {#if run.status === "running"}
                <button class="danger" disabled={busy[run.id]} onclick={() => cancel(run)}>
                  Cancel
                </button>
              {/if}
              <button class="danger" disabled={busy[run.id]} onclick={() => deleteRun(run)}>
                Delete
              </button>
            </td>
          </tr>
          {#if expanded[run.id]}
            <tr>
              <td colspan="9">
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
                          <td>
                            {#if DONE_QUANT_STATUSES.has(qb.status) && qb.hf_repo_id}
                              <a
                                class="quant-done-link"
                                href="https://huggingface.co/{qb.hf_repo_id}"
                                target="_blank"
                                rel="noreferrer"
                              >{qb.quant}</a>
                            {:else}
                              {qb.quant}
                            {/if}
                          </td>
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
          <tr><td colspan="9" class="muted">No runs yet.</td></tr>
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
  .quant-checks {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .quant-done-link {
    font-weight: 700;
    color: var(--success);
    text-decoration: none;
  }
  .quant-done-link:hover {
    text-decoration: underline;
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
