<script>
  let { onclose, children } = $props();

  function handleKeydown(e) {
    if (e.key === "Escape") onclose();
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="modal-backdrop" onclick={onclose} role="presentation">
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    class="modal-panel card"
    onclick={(e) => e.stopPropagation()}
    role="dialog"
    aria-modal="true"
    tabindex="-1"
  >
    <button type="button" class="modal-close" onclick={onclose} aria-label="Close">×</button>
    {@render children()}
  </div>
</div>

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: color-mix(in srgb, black 45%, transparent);
    display: flex;
    align-items: flex-start;
    justify-content: center;
    z-index: 100;
    padding: 8vh 24px 24px;
    overflow-y: auto;
  }

  .modal-panel {
    max-width: 640px;
    width: 100%;
    max-height: 82vh;
    overflow-y: auto;
    padding: 24px;
    position: relative;
  }

  .modal-close {
    position: absolute;
    top: 10px;
    right: 10px;
    border: none;
    background: transparent;
    font-size: 22px;
    line-height: 1;
    padding: 4px 10px;
    color: var(--muted);
  }

  .modal-close:hover {
    color: var(--ink);
    border-color: transparent;
  }
</style>
