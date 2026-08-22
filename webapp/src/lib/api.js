// Thin fetch wrapper for the Orchestrator API. Same-origin in production
// (FastAPI serves this build's static files); proxied to :8000 by Vite's
// dev server (see vite.config.js) during frontend development.

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let body = null;
  let parseError = false;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      parseError = true;
    }
  }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : parseError ? text : res.statusText;
    throw new Error(`${res.status} ${path}: ${detail}`);
  }
  if (parseError) {
    throw new Error(`${path}: invalid JSON response: ${text}`);
  }
  return body;
}

const get = (path) => request(path);
const post = (path, body) =>
  request(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
const patch = (path, body) =>
  request(path, { method: "PATCH", body: JSON.stringify(body) });
const del = (path) => request(path, { method: "DELETE" });

export const api = {
  modelsMflux: () => get("/models_mflux"),
  modelsHf: () => get("/models_hf"),
  modelsHfUpdate: () => post("/models_hf/update"),
  modelsMissing: () => get("/models_missing"),
  modelsMissingUpdate: () => post("/models_missing/update"),
  modelsSrcDetails: () => get("/models_src_details"),
  modelsSrcDetailsUpdate: () => post("/models_src_details/update"),
  modelsIdentity: () => get("/models_identity"),
  modelsAvailable: () => get("/models_available"),
  modelsSkippedRefresh: () => post("/models_skipped/refresh"),
  textEncoderAliases: () => get("/text_encoder_aliases"),

  queueList: () => get("/models_queue"),
  queueAdd: (entry) => post("/models_queue", entry),
  queueUpdate: (id, fields) => patch(`/models_queue/${id}`, fields),
  queueDelete: (id) => del(`/models_queue/${id}`),
  queuePublish: () => post("/models_queue/publish"),
  queueRestore: () => post("/models_queue/restore"),

  datasetsList: () => get("/datasets"),
  datasetPull: (name) => post(`/datasets/${name}/pull`),
  datasetPush: (name) => post(`/datasets/${name}/push`),

  generate: (req) => post("/generate", req),
  generateCancel: (runId) => post(`/generate/${runId}/cancel`),

  report: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return get(qs ? `/report?${qs}` : "/report");
  },
  reportDump: () => get("/report/dump"),
  reportClear: () => del("/report"),
  reportDeleteRun: (runId) => del(`/report/run/${runId}`),

  outboxPoll: () => post("/outbox/poll"),

  gpuStatus: () => get("/gpu/status"),
  gpuHardware: () => get("/gpu/hardware"),
  gpuPause: () => post("/gpu/pause"),
  gpuStart: () => post("/gpu/start"),
  gpuSetHardware: (hardware) => post("/gpu/hardware", { hardware }),
  gpuLogsBuild: () => get("/gpu/logs/build"),
  gpuLogsContainer: () => get("/gpu/logs/container"),
  modelCardPreview: () => get("/models_hf/card_preview"),

  health: () => get("/health"),
};
