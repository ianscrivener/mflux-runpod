<script>
  let { status } = $props();

  const KIND = {
    running: "warn",
    dispatched: "warn",
    pending: "warn",
    approved: "accent",
    complete: "success",
    completed: "success",
    published: "success",
    built: "success",
    uploaded: "success",
    skipped: "",
    skipped_existing: "",
    cancelled: "danger",
    failed: "danger",
    error: "danger",
  };
</script>

<span class="pill {KIND[status] ?? ''}">{status}</span>
