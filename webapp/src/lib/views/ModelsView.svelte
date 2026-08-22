<script>
  import { api } from "../api.js";
  import { huggingFaceSearchUrl } from "../derive.js";
  import Modal from "../components/Modal.svelte";
  import StatusPill from "../components/StatusPill.svelte";

  // id -> {catalog_slug, stem, buildable, hf_repo_slug, model_type,
  // model_family, model_sub_family, quants, missing_quants, expected_repo_ids}
  // -- see app/models_catalog.py::compute_available_models. `id` is the
  // configs/models/*.yaml stem when one exists, else the upstream catalog's
  // own slug for catalog-only entries that have no local config yet
  // (buildable: false -- can't be queued/built, shown for visibility only).
  let available = $state({});
  let srcDetails = $state({}); // config stem -> {size_gb, commit_hash, last_modified, text_encoder} | {error}
  let textEncoderAliases = $state({}); // raw joined text_encoder name -> display alias
  let loading = $state(true);
  let error = $state(null);
  let queuePending = $state({}); // `${id}:${quant}` -> true while POSTing
  let queueMsg = $state({}); // id -> last queue action message

  // Fixed display order for the "Show:" model-type filter row -- only
  // types actually present in the current data render a pill (a type with
  // zero rows would be a dead toggle nobody can click back on).
  const MODEL_TYPE_ORDER = ["image", "video", "depth-estimation", "video-upscale"];
  let hiddenTypes = $state(new Set()); // model_type values currently filtered out
  let hiddenFamilies = $state(new Set()); // model_family values currently filtered out
  let statusFilter = $state(null); // null (all) | "done" | "missing"

  function toggleInSet(set, value) {
    const next = new Set(set);
    if (next.has(value)) {
      next.delete(value);
    } else {
      next.add(value);
    }
    return next;
  }

  async function load() {
    try {
      const [availableRes, srcDetailsRes, aliasesRes] = await Promise.all([
        api.modelsAvailable(),
        api.modelsSrcDetails(),
        api.textEncoderAliases(),
      ]);
      available = availableRes;
      srcDetails = srcDetailsRes;
      textEncoderAliases = aliasesRes;
      error = null;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  load();
  const poll = setInterval(load, 8000);
  $effect(() => () => clearInterval(poll));

  const rows = $derived(
    Object.entries(available)
      .map(([id, info]) => ({
        id,
        stem: info.stem, // null for catalog-only (non-buildable) rows
        buildable: info.buildable,
        slug: info.hf_repo_slug ?? null,
        family: info.model_family ?? "—",
        modelType: info.model_type ?? "—",
        modelSubFamily: info.model_sub_family ?? null,
        quants: info.quants ?? [],
        missingQuants: new Set(info.missing_quants ?? []),
        // srcDetails is keyed by config stem, so this is only ever
        // populated for buildable rows (catalog-only rows have no scan).
        hfModelName: (info.stem ? srcDetails[info.stem]?.hf_model_name : null) ?? null,
      }))
      .sort((a, b) => a.id.localeCompare(b.id))
  );

  const presentTypes = $derived.by(() => {
    const seen = new Set(rows.map((r) => r.modelType).filter((t) => t && t !== "—"));
    return MODEL_TYPE_ORDER.filter((t) => seen.has(t));
  });

  // No fixed order specified for families (unlike model type) -- alphabetical.
  const presentFamilies = $derived.by(() => {
    const seen = new Set(rows.map((r) => r.family).filter((f) => f && f !== "—"));
    return [...seen].sort();
  });

  function toggleType(t) {
    hiddenTypes = toggleInSet(hiddenTypes, t);
  }

  function toggleFamily(f) {
    hiddenFamilies = toggleInSet(hiddenFamilies, f);
  }

  function toggleStatusFilter(v) {
    statusFilter = statusFilter === v ? null : v;
  }

  // "Done" = every expected quant is published (green); "Missing" = at
  // least one expected quant isn't (red/queue-pill, or grey/unsupported for
  // a catalog-only row -- both count as "not done" here).
  function isDone(r) {
    return r.quants.length > 0 && r.missingQuants.size === 0;
  }

  function isMissing(r) {
    return r.missingQuants.size > 0;
  }

  function showAllFamilies() {
    hiddenFamilies = new Set();
  }

  function hideAllFamilies() {
    hiddenFamilies = new Set(presentFamilies);
  }

  async function queueQuant(row, quant) {
    const key = `${row.id}:${quant}`;
    if (queuePending[key]) return;
    queuePending = { ...queuePending, [key]: true };
    queueMsg = { ...queueMsg, [row.id]: `queueing ${quant}…` };
    try {
      await api.queueAdd({ model_stem: row.stem, quants: [quant] });
      queueMsg = { ...queueMsg, [row.id]: `queued ${quant}` };
    } catch (e) {
      queueMsg = { ...queueMsg, [row.id]: e.message };
    } finally {
      queuePending = { ...queuePending, [key]: false };
    }
  }

  let selectedId = $state(null);
  const selectedRow = $derived(rows.find((r) => r.id === selectedId) ?? null);
  const selectedSrc = $derived(selectedId ? srcDetails[selectedId] : null);

  // README metadata is arbitrary per-repo shaped JSON (license, tags,
  // pipeline_tag, gated-access fields, sometimes a widget array...) --
  // rendered generically rather than hand-picking fields, so the modal
  // really does show everything on file, not a curated subset.
  const META_KEY_LABELS = { hf_model_name: "HF model" };
  function metaLabel(key) {
    return META_KEY_LABELS[key] ?? key.replace(/_/g, " ");
  }
  function metaValue(value) {
    if (value == null) return "—";
    if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  // Sortable columns cycle asc -> desc -> default (unsorted, i.e. rows'
  // own alphabetical-by-id order) on repeated clicks of the same header.
  let sortColumn = $state(null); // "family" | "stem" | "gb" | "textEncoder" | null
  let sortDirection = $state("asc");

  function toggleSort(column) {
    if (sortColumn !== column) {
      sortColumn = column;
      sortDirection = "asc";
    } else if (sortDirection === "asc") {
      sortDirection = "desc";
    } else {
      sortColumn = null;
    }
  }

  function ariaSort(column) {
    if (sortColumn !== column) return "none";
    return sortDirection === "asc" ? "ascending" : "descending";
  }

  function textEncoderDisplay(id) {
    const te = srcDetails[id]?.text_encoder;
    if (!te) return "";
    const raw = te.join(" + ");
    return textEncoderAliases[raw] ?? raw;
  }

  function sortValue(row, column) {
    switch (column) {
      case "modelType":
        return row.modelType === "—" ? "" : row.modelType;
      case "family":
        return row.family === "—" ? "" : row.family;
      case "stem":
        return row.id;
      case "gb":
        return srcDetails[row.id]?.size_gb ?? -Infinity;
      case "textEncoder":
        return textEncoderDisplay(row.id);
      default:
        return "";
    }
  }

  const sortedRows = $derived.by(() => {
    if (!sortColumn) return rows; // rows is already sorted alphabetically by id
    const dir = sortDirection === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = sortValue(a, sortColumn);
      const bv = sortValue(b, sortColumn);
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  });

  const visibleRows = $derived(
    sortedRows.filter(
      (r) =>
        !hiddenTypes.has(r.modelType) &&
        !hiddenFamilies.has(r.family) &&
        (statusFilter === "done" ? isDone(r) : statusFilter === "missing" ? isMissing(r) : true)
    )
  );

  // Published-quant detail modal -- fetched on demand (not on every poll)
  // since it's opened rarely. /report/dump has no model_series filter, so
  // filter client-side; that's fine at this project's scale (one series'
  // worth of run history, not thousands of rows).
  let selectedQuant = $state(null); // {stem, quant} | null
  let quantDetail = $state(null);
  let quantDetailLoading = $state(false);
  let quantDetailError = $state(null);

  function bytesToGb(n) {
    return n == null ? null : (n / 1_000_000_000).toFixed(2);
  }

  async function openQuantDetail(row, quant) {
    selectedQuant = { id: row.id, quant };
    quantDetail = null;
    quantDetailError = null;
    quantDetailLoading = true;
    try {
      const [dump, hfRes] = await Promise.all([api.reportDump(), api.modelsHf()]);
      // Build/run history is keyed by model_series (== config stem) --
      // catalog-only rows have no stem, so there's never any history to
      // find for them (correct: they can't have been dispatched from here).
      const seriesRuns = row.stem
        ? (dump.runs ?? [])
            .filter((r) => r.model_series === row.stem)
            .sort((a, b) => b.id - a.id) // newest first
        : [];

      let build = null;
      let run = null;
      for (const r of seriesRuns) {
        const b = (r.quant_builds ?? []).find((qb) => qb.quant === quant);
        if (b) {
          build = b;
          run = r;
          break;
        }
      }

      const publishedName = row.slug ? `mflux-community/${row.slug}-mflux-${quant}` : null;
      const published = (hfRes.hf_models ?? []).find((m) => m.model_name === publishedName) ?? null;

      quantDetail = { build, run, published, publishedName };
    } catch (e) {
      quantDetailError = e.message;
    } finally {
      quantDetailLoading = false;
    }
  }

  function closeQuantDetail() {
    selectedQuant = null;
    quantDetail = null;
    quantDetailError = null;
  }
</script>

<h2 style="margin-bottom:16px">MFlux Format Models</h2>

{#if error}
  <p class="pill danger">{error}</p>
{:else if loading}
  <p class="muted">Loading…</p>
{:else}
  <div class="filter-bar">
    <span class="filter-label muted">Status:</span>
    <button
      type="button"
      class="pill filter-toggle"
      class:off={statusFilter !== "done"}
      onclick={() => toggleStatusFilter("done")}
    >Done</button>
    <button
      type="button"
      class="pill filter-toggle"
      class:off={statusFilter !== "missing"}
      onclick={() => toggleStatusFilter("missing")}
    >Missing</button>
    <span class="filter-separator">|</span>
    <span class="filter-label muted">Image Type:</span>
    {#each presentTypes as t}
      <button
        type="button"
        class="pill filter-toggle"
        class:off={hiddenTypes.has(t)}
        onclick={() => toggleType(t)}
      >{t}</button>
    {/each}
    <span class="filter-separator">|</span>
    <span class="filter-label muted">Model Family:</span>
    <button type="button" class="link-toggle" onclick={showAllFamilies}>All</button>/<button type="button" class="link-toggle" onclick={hideAllFamilies}>None</button>
    {#each presentFamilies as f}
      <button
        type="button"
        class="pill filter-toggle"
        class:off={hiddenFamilies.has(f)}
        onclick={() => toggleFamily(f)}
      >{f}</button>
    {/each}
  </div>
  <div class="card scroll-x">
    <table class="models-table">
      <thead>
        <tr>
          <th>#</th>
          <th aria-sort={ariaSort("modelType")}>
            <button type="button" class="sortable" class:sorted={sortColumn === "modelType"} onclick={() => toggleSort("modelType")}>
              Model Type{#if sortColumn === "modelType"}{sortDirection === "asc" ? " ▲" : " ▼"}{/if}
            </button>
          </th>
          <th aria-sort={ariaSort("family")}>
            <button type="button" class="sortable" class:sorted={sortColumn === "family"} onclick={() => toggleSort("family")}>
              Family{#if sortColumn === "family"}{sortDirection === "asc" ? " ▲" : " ▼"}{/if}
            </button>
          </th>
          <th aria-sort={ariaSort("stem")}>
            <button type="button" class="sortable" class:sorted={sortColumn === "stem"} onclick={() => toggleSort("stem")}>
              Model{#if sortColumn === "stem"}{sortDirection === "asc" ? " ▲" : " ▼"}{/if}
            </button>
          </th>
          <th aria-sort={ariaSort("gb")}>
            <button type="button" class="sortable" class:sorted={sortColumn === "gb"} onclick={() => toggleSort("gb")}>
              GB{#if sortColumn === "gb"}{sortDirection === "asc" ? " ▲" : " ▼"}{/if}
            </button>
          </th>
          <th aria-sort={ariaSort("textEncoder")}>
            <button type="button" class="sortable" class:sorted={sortColumn === "textEncoder"} onclick={() => toggleSort("textEncoder")}>
              Text Encoder{#if sortColumn === "textEncoder"}{sortDirection === "asc" ? " ▲" : " ▼"}{/if}
            </button>
          </th>
          <th>Quants</th>
        </tr>
      </thead>
      <tbody>
        {#each visibleRows as row, i (row.id)}
          <tr>
            <td class="muted">{i + 1}</td>
            <td><span class="pill accent">{row.modelType}</span></td>
            <td><span class="pill accent">{row.family}</span></td>
            <td>
              <button type="button" class="model-name-btn" onclick={() => (selectedId = row.id)}>
                <strong>{row.id}</strong>
              </button>
              {#if !row.buildable}
                <span class="pill faint" title="No configs/models/*.yaml yet -- listed for visibility only, not queueable">catalog only</span>
              {/if}
            </td>
            <td>
              {#if srcDetails[row.id]?.size_gb != null}
                {@const d = srcDetails[row.id]}
                <div title="commit {d.commit_hash?.slice(0, 8)}, last modified {d.last_modified?.slice(0, 10)}">
                  <div style="font-size:12px">{d.size_gb}</div>
                  <div class="muted" style="font-size:9px">{d.size_text_encoder} / {d.size_transformers}</div>
                </div>
              {:else}
                <span class="faint">—</span>
              {/if}
            </td>
            <td>
              {#if srcDetails[row.id]?.text_encoder}
                {@const raw = srcDetails[row.id].text_encoder.join(" + ")}
                <span title={raw}>{textEncoderAliases[raw] ?? raw}</span>
              {:else}
                <span class="faint">—</span>
              {/if}
            </td>
            <td>
              <div class="quant-pills">
                {#each row.quants as q}
                  {#if row.missingQuants.has(q)}
                    {#if row.buildable}
                      <button
                        type="button"
                        class="pill queue-pill"
                        disabled={queuePending[`${row.id}:${q}`]}
                        onclick={() => queueQuant(row, q)}
                        title="{row.id} {q} is missing from Hugging Face -- click to add to the queue"
                      >{q}</button>
                    {:else}
                      <span class="pill unsupported" title="{row.id} {q} is missing, but there's no configs/models/*.yaml yet -- not queueable">{q}</span>
                    {/if}
                  {:else}
                    <button
                      type="button"
                      class="pill published"
                      onclick={() => openQuantDetail(row, q)}
                      title="{row.id} {q} is on Hugging Face -- click for build/generation details"
                    >{q}</button>
                  {/if}
                {/each}
              </div>
              {#if queueMsg[row.id]}
                <div class="muted" style="font-size:10px; margin-top:4px">{queueMsg[row.id]}</div>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  <p class="muted" style="font-size:12px; margin-top:12px">
    Solid green = published to Hugging Face. Outlined = missing -- click to queue it via POST /models_queue
    (this does not build anything by itself; approve + process from the Queue tab). Grayed = missing but not
    yet buildable (no configs/models/*.yaml for it). Click a model's name for full details.
  </p>
{/if}

{#if selectedRow}
  <Modal onclose={() => (selectedId = null)}>
    <h2 style="margin-bottom:2px">{selectedRow.id}</h2>
    <div style="display:flex; gap:6px; flex-wrap:wrap; margin-bottom:16px">
      <span class="pill accent">{selectedRow.family}</span>
      {#if selectedRow.modelType && selectedRow.modelType !== "—"}<span class="pill">{selectedRow.modelType}</span>{/if}
      {#if selectedRow.modelSubFamily}<span class="pill">{selectedRow.modelSubFamily}</span>{/if}
      {#if !selectedRow.buildable}<span class="pill faint">catalog only -- no configs/models/*.yaml yet</span>{/if}
    </div>

    <div style="display:flex; gap:16px; flex-wrap:wrap; margin-bottom:16px">
      {#if selectedRow.hfModelName}
        <a href="https://huggingface.co/{selectedRow.hfModelName}" target="_blank" rel="noreferrer">
          View source repo on Hugging Face ↗
        </a>
      {/if}
      {#if selectedRow.slug}
        <a href={huggingFaceSearchUrl(selectedRow.slug)} target="_blank" rel="noreferrer">
          Search our published quants ↗
        </a>
      {/if}
    </div>

    {#if selectedSrc?.error}
      <p class="pill danger">scan error: {selectedSrc.error}</p>
    {:else if selectedSrc}
      <table class="detail-table">
        <tbody>
          <tr><td class="muted">Source repo</td><td>{selectedSrc.hf_model_name ?? "—"}</td></tr>
          <tr><td class="muted">Total size</td><td>{selectedSrc.size_gb ?? "—"} GB</td></tr>
          <tr>
            <td class="muted">Text encoder / Transformers</td>
            <td>{selectedSrc.size_text_encoder ?? "—"} / {selectedSrc.size_transformers ?? "—"} GB</td>
          </tr>
          <tr>
            <td class="muted">Text encoder</td>
            <td>
              {#if selectedSrc.text_encoder}
                {@const raw = selectedSrc.text_encoder.join(" + ")}
                {textEncoderAliases[raw] ?? raw}
              {:else}
                —
              {/if}
            </td>
          </tr>
          <tr><td class="muted">Commit</td><td class="mono">{selectedSrc.commit_hash ?? "—"}</td></tr>
          <tr><td class="muted">Last modified</td><td>{selectedSrc.last_modified ?? "—"}</td></tr>
          {#if selectedSrc.readme_meta}
            {#each Object.entries(selectedSrc.readme_meta) as [key, value]}
              <tr><td class="muted">{metaLabel(key)}</td><td>{metaValue(value)}</td></tr>
            {/each}
          {/if}
        </tbody>
      </table>
    {:else}
      <p class="muted">No source-repo scan on file for this model yet.</p>
    {/if}
  </Modal>
{/if}

{#if selectedQuant}
  <Modal onclose={closeQuantDetail}>
    <h2 style="margin-bottom:2px">{selectedQuant.id} — {selectedQuant.quant}</h2>
    <p class="muted" style="font-size:12px; margin-bottom:16px">Published quantized model</p>

    {#if quantDetailLoading}
      <p class="muted">Loading…</p>
    {:else if quantDetailError}
      <p class="pill danger">{quantDetailError}</p>
    {:else if quantDetail}
      {#if quantDetail.published}
        <div style="margin-bottom:12px">
          <a href="https://huggingface.co/{quantDetail.published.model_name}" target="_blank" rel="noreferrer">
            View on Hugging Face ↗
          </a>
        </div>
        <table class="detail-table">
          <tbody>
            <tr><td class="muted">Published repo</td><td class="mono">{quantDetail.published.model_name}</td></tr>
            <tr><td class="muted">Published size</td><td>{quantDetail.published.size_gb != null ? quantDetail.published.size_gb.toFixed(2) : "—"} GB</td></tr>
            <tr><td class="muted">Upload date</td><td>{quantDetail.published.upload_date ?? "—"}</td></tr>
            <tr><td class="muted">Uploaded by</td><td>{quantDetail.published.upload_user ?? "—"}</td></tr>
            <tr><td class="muted">Commit</td><td class="mono">{quantDetail.published.commit_hash ?? "—"}</td></tr>
          </tbody>
        </table>
      {:else}
        <p class="muted" style="margin-bottom:12px">
          Not found in our published-quants manifest (models_hf.json) — it may need a refresh
          via the Admin menu.
        </p>
      {/if}

      <h3 style="margin:20px 0 8px; font-size:13px">Generation</h3>
      {#if quantDetail.build}
        <table class="detail-table">
          <tbody>
            <tr>
              <td class="muted">Status</td>
              <td><StatusPill status={quantDetail.build.status} /></td>
            </tr>
            <tr><td class="muted">Run started</td><td>{quantDetail.run?.started_at ?? "—"}</td></tr>
            <tr><td class="muted">Run finished</td><td>{quantDetail.run?.finished_at ?? "—"}</td></tr>
            <tr>
              <td class="muted">Build duration</td>
              <td>{quantDetail.build.build_duration_s != null ? `${quantDetail.build.build_duration_s.toFixed(1)}s` : "—"}</td>
            </tr>
            <tr>
              <td class="muted">Upload duration</td>
              <td>{quantDetail.build.upload_duration_s != null ? `${quantDetail.build.upload_duration_s.toFixed(1)}s` : "—"}</td>
            </tr>
            <tr><td class="muted">Total size</td><td>{bytesToGb(quantDetail.build.total_size_bytes) ?? "—"} GB</td></tr>
            <tr>
              <td class="muted">Text encoder / Transformer / VAE</td>
              <td>
                {bytesToGb(quantDetail.build.text_encoder_bytes) ?? "—"} /
                {bytesToGb(quantDetail.build.transformer_bytes) ?? "—"} /
                {bytesToGb(quantDetail.build.vae_bytes) ?? "—"} GB
              </td>
            </tr>
            <tr><td class="muted">HF repo ID</td><td class="mono">{quantDetail.build.hf_repo_id ?? "—"}</td></tr>
            <tr>
              <td class="muted">MFlux repo</td>
              <td>{quantDetail.run?.mflux_repo ?? "default"}{quantDetail.run?.mflux_branch ? ` @ ${quantDetail.run.mflux_branch}` : ""}</td>
            </tr>
            <tr><td class="muted">Machine type</td><td>{quantDetail.run?.machine_type ?? "not recorded"}</td></tr>
            <tr><td class="muted">Cost</td><td class="faint">not tracked yet — no GPU-rate/duration wiring on file</td></tr>
          </tbody>
        </table>
      {:else}
        <p class="muted">
          No local build/run history on file for this quant — it may have been published
          outside this Orchestrator, or its run log was cleared.
        </p>
      {/if}
    {/if}
  </Modal>
{/if}

<style>
  .filter-bar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 5px;
    margin-bottom: 12px;
  }

  .filter-label {
    font-size: 12px;
    font-weight: 700;
  }

  .filter-separator {
    color: var(--muted);
  }

  .link-toggle {
    font-family: inherit;
    font-size: 12px;
    background: none;
    border: none;
    padding: 0;
    color: var(--accent);
    text-decoration: underline;
    cursor: pointer;
  }

  .pill.filter-toggle {
    cursor: pointer;
    color: var(--success);
    border-color: color-mix(in srgb, var(--success) 55%, transparent);
    background: color-mix(in srgb, var(--success) 10%, transparent);
  }

  .pill.filter-toggle:hover {
    background: color-mix(in srgb, var(--success) 18%, transparent);
  }

  .pill.filter-toggle.off {
    background: var(--surface-2);
    color: var(--faint);
    border-color: var(--border);
    opacity: 0.6;
  }

  .models-table th:nth-child(5),
  .models-table td:nth-child(5) {
    text-align: center;
  }

  .models-table button.sortable {
    all: unset;
    cursor: pointer;
    user-select: none;
    font: inherit;
    color: inherit;
  }

  .models-table button.sortable:hover {
    color: var(--ink);
  }

  .models-table button.sortable.sorted {
    color: var(--accent);
  }

  .models-table button.sortable:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .quant-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .model-name-btn {
    border: none;
    background: none;
    padding: 0;
    font: inherit;
    color: inherit;
    cursor: pointer;
  }

  .model-name-btn:hover strong {
    color: var(--accent);
    text-decoration: underline;
  }

  .detail-table td {
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
    white-space: normal;
    vertical-align: top;
  }

  .detail-table td:first-child {
    width: 40%;
    padding-right: 12px;
  }

  .detail-table tr:last-child td {
    border-bottom: none;
  }
</style>
