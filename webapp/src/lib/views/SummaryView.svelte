<script>
  import { api } from "../api.js";

  // id -> {buildable, hf_repo_slug, model_family, missing_quants, ...} --
  // see app/models_catalog.py::compute_available_models. Same source the
  // Models page uses, filtered to buildable:true here so this stat and that
  // table always agree (both previously read /models_missing, which never
  // knew about models_skipped.json's exclusions or catalog-only entries).
  let available = $state({});
  let hfModels = $state([]);
  let loading = $state(true);
  let error = $state(null);

  async function load() {
    try {
      const [availableRes, hfRes] = await Promise.all([api.modelsAvailable(), api.modelsHf()]);
      available = availableRes;
      hfModels = hfRes.hf_models ?? [];
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

  const stats = $derived.by(() => {
    const buildable = Object.values(available).filter((v) => v.buildable);
    const supportedFamilies = new Set(buildable.map((v) => v.model_family).filter(Boolean));
    const supportedQuants = buildable.reduce((n, v) => n + v.missing_quants.length, 0);

    // Published HF repo names embed OUR OWN publish slug (hf_repo_slug),
    // not the upstream catalog's slug -- reverse-index by that field to
    // resolve family for the "Hugging Face" row below.
    const byHfRepoSlug = {};
    for (const v of buildable) {
      if (v.hf_repo_slug) byHfRepoSlug[v.hf_repo_slug] = v;
    }
    const hfFamilies = new Set();
    const hfSlugs = new Set();
    for (const m of hfModels) {
      const name = m.model_name.includes("/") ? m.model_name.split("/")[1] : m.model_name;
      const match = name.match(/^(.+)-mflux-(q3|q4|q5|q6|q8|bf16)$/);
      if (!match) continue;
      hfSlugs.add(match[1]);
      const fam = byHfRepoSlug[match[1]]?.model_family;
      if (fam) hfFamilies.add(fam);
    }

    return {
      supported: { families: supportedFamilies.size, models: buildable.length, quants: supportedQuants },
      published: { families: hfFamilies.size, models: hfSlugs.size, quants: hfModels.length },
    };
  });
</script>

<h2 style="margin-bottom:16px">Summary</h2>

{#if error}
  <p class="pill danger">{error}</p>
{:else if loading}
  <p class="muted">Loading…</p>
{:else}
  <div class="hero-matrix">
    <div class="hero-corner"></div>
    <div class="hero-col-label">Models Families</div>
    <div class="hero-col-label">Distinct Models</div>
    <div class="hero-col-label">Quantized Models</div>

    <div class="hero-row-label mflux">MFlux supported</div>
    <div class="hero-tile mflux">
      <span class="hero-value">{stats.supported.families}</span>
    </div>
    <div class="hero-tile mflux">
      <span class="hero-value">{stats.supported.models}</span>
    </div>
    <div class="hero-tile mflux">
      <span class="hero-value">{stats.supported.quants}</span>
    </div>

    <div class="hero-row-label hf">MFlux on HuggingFace</div>
    <div class="hero-tile hf">
      <span class="hero-value">{stats.published.families}</span>
    </div>
    <div class="hero-tile hf">
      <span class="hero-value">{stats.published.models}</span>
    </div>
    <div class="hero-tile hf">
      <span class="hero-value">{stats.published.quants}</span>
    </div>
  </div>
  <p class="muted" style="font-size:12px; margin-top:20px; max-width:640px">
    <strong>MFlux Supported</strong> — every buildable model series (has a configs/models/*.yaml)
    and the quants still missing from Hugging Face. <strong>Hugging Face</strong> — what's actually
    published on mflux-community right now.
  </p>
{/if}

<style>
  .hero-matrix {
    display: grid;
    grid-template-columns: minmax(120px, auto) repeat(3, minmax(140px, 1fr));
    gap: 14px;
    max-width: 900px;
  }

  .hero-col-label {
    align-self: end;
    padding-bottom: 6px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    text-align: center;
  }

  .hero-row-label {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-weight: 600;
    font-size: 14px;
    line-height: 1.25;
  }

  .hero-row-label.mflux {
    color: var(--accent);
  }

  .hero-row-label.hf {
    color: var(--success);
  }

  .hero-tile {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    box-shadow: var(--shadow);
    padding: 32px 16px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .hero-value {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
    font-weight: 600;
    font-size: 42px;
    line-height: 1;
    font-variant-numeric: tabular-nums;
  }

  .hero-tile.mflux {
    border-color: color-mix(in srgb, var(--accent) 35%, var(--border));
  }

  .hero-tile.mflux .hero-value {
    color: var(--accent);
  }

  .hero-tile.hf {
    border-color: color-mix(in srgb, var(--success) 35%, var(--border));
  }

  .hero-tile.hf .hero-value {
    color: var(--success);
  }

  @media (max-width: 640px) {
    .hero-matrix {
      grid-template-columns: minmax(90px, auto) repeat(3, 1fr);
      gap: 8px;
    }

    .hero-tile {
      padding: 20px 8px;
    }

    .hero-value {
      font-size: 28px;
    }
  }
</style>
