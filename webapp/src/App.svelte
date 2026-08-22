<script>
  import { api } from "./lib/api.js";
  import ModelsView from "./lib/views/ModelsView.svelte";
  import QueueView from "./lib/views/QueueView.svelte";
  import RunsView from "./lib/views/RunsView.svelte";
  import GpuView from "./lib/views/GpuView.svelte";
  import DatasetsView from "./lib/views/DatasetsView.svelte";

  const NAV_TABS = [
    { id: "models", label: "Models", component: ModelsView },
    { id: "queue", label: "Queue", component: QueueView },
    { id: "runs", label: "Generate", component: RunsView },
    { id: "gpu", label: "GPU", component: GpuView },
  ];

  const ADMIN_TABS = [{ id: "datasets", label: "Datasets", component: DatasetsView }];

  const TABS = [...NAV_TABS, ...ADMIN_TABS];

  let active = $state("models");
  let online = $state(true);
  let adminOpen = $state(false);

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
      <h1>MFlux-Conv</h1>
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
          </div>
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

  main {
    flex: 1;
    padding: 24px;
    max-width: 1400px;
    width: 100%;
    margin: 0 auto;
  }
</style>
