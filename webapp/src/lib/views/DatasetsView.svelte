<script>
  import { api } from "../api.js";

  let datasets = $state([]);
  let loading = $state(true);
  let error = $state(null);
  let busy = $state({});
  let msg = $state({});

  // Only these four datasets are actually computed/scraped by this app and
  // have a real "regenerate" endpoint -- everything else in configs/
  // hf_datasets.yaml is either pull-only (models_mflux, owned by upstream
  // mflux CI), human-authored with no generate step (models_queue,
  // models_skipped), or an append-only log (logs_devops, logs_conversions).
  const REFRESH_ACTIONS = {
    models_hf: api.modelsHfUpdate,
    models_missing: api.modelsMissingUpdate,
    models_src_details: api.modelsSrcDetailsUpdate,
    runpod_gpu_skus: api.runpodGpuSkusUpdate,
  };

  async function load() {
    try {
      const res = await api.datasetsList();
      datasets = res.datasets;
      error = null;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  load();
  const poll = setInterval(load, 10000);
  $effect(() => () => clearInterval(poll));

  async function pull(name) {
    busy = { ...busy, [name]: true };
    try {
      const res = await api.datasetPull(name);
      msg = { ...msg, [name]: res.changed ? "pulled (changed)" : "up to date" };
      await load();
    } catch (e) {
      msg = { ...msg, [name]: e.message };
    } finally {
      busy = { ...busy, [name]: false };
    }
  }

  async function push(name) {
    busy = { ...busy, [name]: true };
    try {
      await api.datasetPush(name);
      msg = { ...msg, [name]: "pushed" };
      await load();
    } catch (e) {
      msg = { ...msg, [name]: e.message };
    } finally {
      busy = { ...busy, [name]: false };
    }
  }

  async function refresh(name) {
    busy = { ...busy, [name]: true };
    try {
      await REFRESH_ACTIONS[name]();
      msg = { ...msg, [name]: "refreshed" };
      await load();
    } catch (e) {
      msg = { ...msg, [name]: e.message };
    } finally {
      busy = { ...busy, [name]: false };
    }
  }
</script>

<h2 style="margin-bottom:12px">Datasets</h2>

{#if error}
  <p class="pill danger">{error}</p>
{:else if loading}
  <p class="muted">Loading…</p>
{:else}
  <div class="card scroll-x">
    <table>
      <thead>
        <tr><th>Name</th><th>Path in repo</th><th>Local</th><th>Writable</th><th>Last known</th><th>Refresh</th><th></th></tr>
      </thead>
      <tbody>
        {#each datasets as d (d.name)}
          <tr>
            <td><strong>{d.name}</strong></td>
            <td class="muted">{d.path_in_repo}</td>
            <td>
              {#if d.local_exists}
                <span class="pill success">present</span>
                <span class="faint" style="font-size:11px">{d.local_mtime?.slice(0, 19).replace("T", " ")}</span>
              {:else}
                <span class="pill danger">missing</span>
              {/if}
            </td>
            <td>{d.writable ? "yes" : "no"}</td>
            <td class="muted" style="font-size:11px">
              {d.last_known ? (d.last_known.xet_hash?.slice(0, 10) ?? JSON.stringify(d.last_known)) : "—"}
            </td>
            <td>
              {#if REFRESH_ACTIONS[d.name]}
                <button
                  type="button"
                  class="refresh-btn"
                  disabled={busy[d.name]}
                  onclick={() => refresh(d.name)}
                  title="Regenerate {d.name} from its source and publish it"
                  aria-label="Refresh {d.name}"
                >🔄</button>
              {/if}
            </td>
            <td>
              <button disabled={busy[d.name]} onclick={() => pull(d.name)}>Pull</button>
              <button disabled={busy[d.name] || !d.writable} onclick={() => push(d.name)}>Push</button>
              {#if msg[d.name]}<span class="muted" style="font-size:11px">{msg[d.name]}</span>{/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

<style>
  .refresh-btn {
    all: unset;
    cursor: pointer;
    font-size: 15px;
    line-height: 1;
    padding: 2px 4px;
  }

  .refresh-btn:disabled {
    cursor: wait;
    opacity: 0.5;
  }

  .refresh-btn:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
