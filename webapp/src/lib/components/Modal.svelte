<script>
  let { onclose, children } = $props();

  let panelEl = $state();
  let labelledBy = $state(undefined);

  function focusableElements() {
    if (!panelEl) return [];
    return Array.from(
      panelEl.querySelectorAll(
        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
    );
  }

  function trapFocus(e) {
    const focusables = focusableElements();
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  function handleKeydown(e) {
    if (e.key === "Escape") {
      onclose();
      return;
    }
    if (e.key === "Tab") trapFocus(e);
  }

  // Move focus into the dialog on open, label it from its own content's
  // first heading (every current usage renders one as its first child), and
  // restore focus to whatever triggered it when the dialog closes/unmounts.
  $effect(() => {
    const opener = document.activeElement;

    const heading = panelEl?.querySelector("h1, h2, h3, h4, h5, h6");
    if (heading) {
      if (!heading.id) heading.id = `modal-title-${Math.random().toString(36).slice(2)}`;
      labelledBy = heading.id;
    }

    panelEl?.focus();

    return () => {
      opener?.focus?.();
    };
  });
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="modal-backdrop" onclick={onclose} role="presentation">
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div
    bind:this={panelEl}
    class="modal-panel card"
    onclick={(e) => e.stopPropagation()}
    role="dialog"
    aria-modal="true"
    aria-labelledby={labelledBy}
    aria-label={labelledBy ? undefined : "Dialog"}
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
