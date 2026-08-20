<script>
  import { api } from "../api.js";

  let volumes = $state([]);
  let loading = $state(true);
  let error = $state(null);

  async function load() {
    try {
      const res = await api.modelStore();
      volumes = res.volumes;
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
</script>

<h2 style="margin-bottom:4px">Model Store</h2>
<p class="muted" style="font-size:12px; margin:0 0 12px 0">
  Active ephemeral per-series RunPod build volumes — build-scratch space, not the finished model catalog (that's the Hugging Face org, see Models).
</p>

{#if error}
  <p class="pill danger">{error}</p>
{:else if loading}
  <p class="muted">Loading…</p>
{:else}
  <div class="card scroll-x">
    <table>
      <thead>
        <tr><th>Series</th><th>Volume</th><th>Volume ID</th><th>Size</th><th>Created</th></tr>
      </thead>
      <tbody>
        {#each volumes as v}
          <tr>
            <td><strong>{v.model_series ?? "—"}</strong></td>
            <td class="muted">{v.volume_name ?? v.name ?? "—"}</td>
            <td class="muted" style="font-size:11px">{v.volume_id ?? v.id ?? "—"}</td>
            <td class="muted">{v.runpod?.size_gb ? `${v.runpod.size_gb} GB` : "—"}</td>
            <td class="muted" style="font-size:11px">{(v.created_at ?? "").slice(0, 19).replace("T", " ") || "—"}</td>
          </tr>
        {/each}
        {#if volumes.length === 0}
          <tr><td colspan="5" class="muted">No active build volumes.</td></tr>
        {/if}
      </tbody>
    </table>
  </div>
{/if}
