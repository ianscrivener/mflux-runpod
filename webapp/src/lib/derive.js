// Small helpers to bridge gaps the API doesn't directly expose (documented
// in docs/prd-web-app.md's "Gap" callouts) using data it DOES give us.
//
// Slug/family resolution used to live here as client-side guesswork
// (regex-parsing expected_repo_ids, slugify(stem)) -- removed 2026-08-20 in
// favor of GET /models_identity, which resolves it authoritatively
// server-side (see app/models_catalog.py::get_model_identities). That guessing
// was silently wrong for more than half of all models (our local config
// naming and the upstream mflux catalog's naming are two genuinely
// independent schemes), which is why it's gone rather than kept as a
// fallback.

export function huggingFaceSearchUrl(slug) {
  return `https://huggingface.co/models?search=mflux-community%2F${encodeURIComponent(slug)}-mflux`;
}
