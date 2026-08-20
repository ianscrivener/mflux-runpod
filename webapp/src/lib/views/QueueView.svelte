<script>
  import { api } from "../api.js";

  const STATUSES = ["pending", "approved", "skipped"];

  let entries = $state([]);
  let stems = $state([]);
  let loading = $state(true);
  let error = $state(null);
  let busy = $state({}); // entry id -> bool
  let syncMsg = $state(null);

  let newStem = $state("");
  let newQuants = $state("");
  let newNote = $state("");
  let newForceOverwrite = $state(false);
  let adding = $state(false);

  async function load() {
    try {
      const [queueRes, missingRes] = await Promise.all([api.queueList(), api.modelsMissing()]);
      entries = queueRes.entries;
      stems = [...Object.keys(missingRes.missing), ...missingRes.complete].sort();
      if (!newStem && stems.length) newStem = stems[0];
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

  function parseQuants(text) {
    const trimmed = text.trim();
    if (!trimmed) return null;
    return trimmed.split(",").map((s) => s.trim()).filter(Boolean);
  }

  async function addEntry() {
    if (!newStem) return;
    adding = true;
    try {
      await api.queueAdd({
        model_stem: newStem,
        quants: parseQuants(newQuants),
        force_hf_overwrite: newForceOverwrite,
        note: newNote || null,
      });
      newQuants = "";
      newNote = "";
      newForceOverwrite = false;
      await load();
    } catch (e) {
      error = e.message;
    } finally {
      adding = false;
    }
  }

  async function setStatus(entry, status) {
    busy = { ...busy, [entry.id]: true };
    try {
      await api.queueUpdate(entry.id, { status });
      await load();
    } catch (e) {
      error = e.message;
    } finally {
      busy = { ...busy, [entry.id]: false };
    }
  }

  async function saveQuants(entry, text) {
    busy = { ...busy, [entry.id]: true };
    try {
      await api.queueUpdate(entry.id, { quants: parseQuants(text) });
      await load();
    } catch (e) {
      error = e.message;
    } finally {
      busy = { ...busy, [entry.id]: false };
    }
  }

  async function saveNote(entry, text) {
    busy = { ...busy, [entry.id]: true };
    try {
      await api.queueUpdate(entry.id, { note: text || null });
      await load();
    } catch (e) {
      error = e.message;
    } finally {
      busy = { ...busy, [entry.id]: false };
    }
  }

  async function removeEntry(entry) {
    if (!confirm(`Delete queue entry #${entry.id} (${entry.model_stem})? This cannot be undone.`)) return;
    busy = { ...busy, [entry.id]: true };
    try {
      await api.queueDelete(entry.id);
      await load();
    } catch (e) {
      error = e.message;
      busy = { ...busy, [entry.id]: false };
    }
  }

  async function doPublish() {
    syncMsg = "publishing…";
    try {
      const res = await api.queuePublish();
      syncMsg = res.published === false ? `publish failed: ${res.publish_error}` : "published to HF-bucket master";
    } catch (e) {
      syncMsg = e.message;
    }
  }

  async function doRestore() {
    if (!confirm("Overwrite the local queue with the HF-bucket master copy?")) return;
    syncMsg = "restoring…";
    try {
      await api.queueRestore();
      syncMsg = "restored from HF-bucket master";
      await load();
    } catch (e) {
      syncMsg = e.message;
    }
  }
</script>

<div class="header-row">
  <h2>Queue</h2>
  <div class="sync-actions">
    <button onclick={doPublish}>Publish →</button>
    <button onclick={doRestore}>← Restore</button>
    {#if syncMsg}<span class="muted" style="font-size:12px">{syncMsg}</span>{/if}
  </div>
</div>

{#if error}<p class="pill danger">{error}</p>{/if}

<div class="card add-form">
  <select bind:value={newStem}>
    {#each stems as s}<option value={s}>{s}</option>{/each}
  </select>
  <input placeholder="quants (comma-sep, blank = whatever's missing)" bind:value={newQuants} style="flex:1" />
  <input placeholder="note" bind:value={newNote} style="flex:1" />
  <label class="check-inline"><input type="checkbox" bind:checked={newForceOverwrite} /> force overwrite</label>
  <button class="primary" onclick={addEntry} disabled={adding || !newStem}>Add</button>
</div>

{#if loading}
  <p class="muted">Loading…</p>
{:else}
  <div class="card scroll-x">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Model</th><th>Status</th><th>Quants</th><th>Force</th><th>Note</th><th>Added</th><th></th>
        </tr>
      </thead>
      <tbody>
        {#each entries as entry (entry.id)}
          <tr>
            <td class="muted">{entry.id}</td>
            <td><strong>{entry.model_stem}</strong></td>
            <td>
              <select value={entry.status} disabled={busy[entry.id]} onchange={(e) => setStatus(entry, e.target.value)}>
                {#each STATUSES as s}<option value={s}>{s}</option>{/each}
              </select>
            </td>
            <td>
              <input
                value={entry.quants ? entry.quants.join(", ") : ""}
                placeholder="(missing)"
                disabled={busy[entry.id]}
                onblur={(e) => saveQuants(entry, e.target.value)}
                style="width:140px"
              />
            </td>
            <td>
              {#if entry.force_hf_overwrite}<span class="pill warn">yes</span>{:else}<span class="faint">no</span>{/if}
            </td>
            <td>
              <input
                value={entry.note ?? ""}
                disabled={busy[entry.id]}
                onblur={(e) => saveNote(entry, e.target.value)}
                style="width:160px"
              />
            </td>
            <td class="muted" style="font-size:11px">{entry.added_at?.slice(0, 16).replace("T", " ")}</td>
            <td><button class="danger" disabled={busy[entry.id]} onclick={() => removeEntry(entry)}>Delete</button></td>
          </tr>
        {/each}
        {#if entries.length === 0}
          <tr><td colspan="8" class="muted">Queue is empty.</td></tr>
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
    margin-bottom: 12px;
  }
  .sync-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .add-form {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 12px;
    margin-bottom: 16px;
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
</style>
