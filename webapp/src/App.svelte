<script>
  import { api } from "./lib/api.js";
  import SummaryView from "./lib/views/SummaryView.svelte";
  import ModelsView from "./lib/views/ModelsView.svelte";
  import QueueView from "./lib/views/QueueView.svelte";
  import RunsView from "./lib/views/RunsView.svelte";
  import DatasetsView from "./lib/views/DatasetsView.svelte";
  import ModelStoreView from "./lib/views/ModelStoreView.svelte";

  const NAV_TABS = [
    { id: "summary", label: "Summary", component: SummaryView },
    { id: "models", label: "Models", component: ModelsView },
    { id: "queue", label: "Queue", component: QueueView },
    { id: "runs", label: "Runs / Generate", component: RunsView },
    { id: "store", label: "Volumes", component: ModelStoreView },
  ];

  const ADMIN_TABS = [{ id: "datasets", label: "Datasets", component: DatasetsView }];

  const TABS = [...NAV_TABS, ...ADMIN_TABS];

  let active = $state("summary");
  let online = $state(true);
  let adminOpen = $state(false);
  let skippedRefreshing = $state(false);
  let skippedRefreshMsg = $state("");

  const activeTab = $derived(TABS.find((t) => t.id === active));

  async function checkHealth() {
    try {
      await api.health();
      online = true;
    } catch {
      online = false;
    }
  }

  checkHealth();
  setInterval(checkHealth, 10_000);

  function selectAdminTab(id) {
    active = id;
    adminOpen = false;
  }

  // data-hf-sync/models_skipped.json is hand-edited on disk (not synced to
  // the HF bucket -- see app/models_missing.py::load_models_skipped), and
  // already reads live on every /models_available call, so this force-
  // rebuild isn't strictly required for propagation -- it exists as a
  // tangible "did my edit take effect" confirmation in the Admin menu.
  async function refreshSkippedModels() {
    skippedRefreshing = true;
    skippedRefreshMsg = "";
    try {
      const result = await api.modelsSkippedRefresh();
      skippedRefreshMsg = `refreshed -- ${Object.keys(result).length} models available`;
    } catch (e) {
      skippedRefreshMsg = e.message;
    } finally {
      skippedRefreshing = false;
      adminOpen = false;
    }
  }

  function handleWindowClick(e) {
    if (adminOpen && !e.target.closest(".admin-menu")) adminOpen = false;
  }

  function handleWindowKeydown(e) {
    if (e.key === "Escape") adminOpen = false;
  }
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<div class="shell">
  <header class="topbar">
    <div class="topbar-inner">
      <h1>MFlux Orchestrator</h1>
      <nav>
        {#each NAV_TABS as tab (tab.id)}
          <button
            class:active={active === tab.id}
            onclick={() => (active = tab.id)}
          >
            {tab.label}
          </button>
        {/each}
      </nav>
      <div class="status" class:offline={!online}>
        <span class="dot"></span>
        {online ? "online" : "offline"}
      </div>
      <div class="admin-menu">
        <button
          type="button"
          class="admin-toggle"
          class:active={adminOpen || ADMIN_TABS.some((t) => t.id === active)}
          onclick={() => (adminOpen = !adminOpen)}
          aria-haspopup="true"
          aria-expanded={adminOpen}
        >
          Admin ▾
        </button>
        {#if adminOpen}
          <div class="admin-dropdown">
            {#each ADMIN_TABS as tab (tab.id)}
              <button type="button" class:active={active === tab.id} onclick={() => selectAdminTab(tab.id)}>
                {tab.label}
              </button>
            {/each}
            <div class="admin-divider"></div>
            <button
              type="button"
              disabled={skippedRefreshing}
              onclick={refreshSkippedModels}
              title="Re-read data-hf-sync/models_skipped.json and rebuild the models catalog"
            >
              {skippedRefreshing ? "Refreshing…" : "Refresh Skip Models"}
            </button>
          </div>
        {/if}
        {#if skippedRefreshMsg}
          <div class="admin-toast muted">{skippedRefreshMsg}</div>
        {/if}
      </div>
    </div>
  </header>

  <main>
    {#if activeTab}
      <activeTab.component />
    {/if}
  </main>
</div>

<style>
  .shell {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
  }

  .topbar {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 10;
  }

  .topbar-inner {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 14px 24px;
    max-width: 1400px;
    width: 100%;
    margin: 0 auto;
  }

  .topbar h1 {
    font-size: 16px;
    white-space: nowrap;
  }

  nav {
    display: flex;
    gap: 4px;
    flex: 1;
    flex-wrap: wrap;
  }

  nav button {
    border: 1px solid transparent;
    background: transparent;
    color: var(--muted);
    font-family: "IBM Plex Sans", sans-serif;
  }

  nav button:hover {
    color: var(--ink);
    border-color: var(--border);
  }

  nav button.active {
    background: var(--surface-2);
    color: var(--accent);
    border-color: var(--border);
  }

  .status {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }

  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--success);
  }

  .status.offline .dot {
    background: var(--danger);
  }

  .admin-menu {
    position: relative;
  }

  .admin-toggle {
    border: 1px solid transparent;
    background: transparent;
    color: var(--muted);
    font-family: "IBM Plex Sans", sans-serif;
    font-size: 13px;
  }

  .admin-toggle:hover,
  .admin-toggle.active {
    color: var(--accent);
    border-color: var(--border);
  }

  .admin-dropdown {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    display: flex;
    flex-direction: column;
    min-width: 160px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: var(--shadow);
    padding: 6px;
    z-index: 20;
  }

  .admin-dropdown button {
    border: 1px solid transparent;
    background: transparent;
    color: var(--muted);
    font-family: "IBM Plex Sans", sans-serif;
    text-align: left;
    padding: 8px 10px;
    border-radius: 6px;
  }

  .admin-dropdown button:hover {
    color: var(--ink);
    background: var(--surface-2);
  }

  .admin-dropdown button.active {
    color: var(--accent);
    background: var(--surface-2);
  }

  .admin-dropdown button:disabled {
    cursor: wait;
    opacity: 0.6;
  }

  .admin-divider {
    height: 1px;
    background: var(--border);
    margin: 4px 2px;
  }

  .admin-toast {
    position: absolute;
    top: calc(100% + 6px);
    right: 24px;
    font-size: 11px;
    white-space: nowrap;
  }

  main {
    flex: 1;
    padding: 24px;
    max-width: 1400px;
    width: 100%;
    margin: 0 auto;
  }
</style>
