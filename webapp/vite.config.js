import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Orchestrator API route prefixes -- proxied to the local FastAPI dev
// server so `npm run dev` can hit real data without CORS. In production
// this app is built to ../app/static and served by FastAPI itself (see
// app/main.py's StaticFiles mount), so no proxy is needed there.
const API_PREFIXES = [
  "models_mflux",
  "models_hf",
  "models_missing",
  "models_src_details",
  "models_identity",
  "models_available",
  "models_skipped",
  "text_encoder_aliases",
  "models_queue",
  "runpod_gpu_skus",
  "datasets",
  "model_store",
  "generate",
  "generate_all",
  "report",
  "outbox",
  "health",
];

export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
  },
  server: {
    proxy: Object.fromEntries(
      API_PREFIXES.map((p) => [
        `^/${p}`,
        { target: "http://127.0.0.1:8000", changeOrigin: true },
      ])
    ),
  },
});
