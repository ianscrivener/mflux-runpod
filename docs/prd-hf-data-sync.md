# HF-hosted dataset sync layer

### Context

Across this session we designed a shift in where the Orchestrator's state
lives: instead of data/models_mflux.json being a hand-curated snapshot and
data/models_hf.json/models_missing being purely local caches, all of it
becomes durable on Hugging Face — partly because HF dataset repos are git
repos (free version history/audit trail), partly so the Orchestrator's local
disk becomes disposable ("ephemeral Orchestrator": lose the volume, re-pull
from HF, you're whole again).                                                                                                                                                                                                 
Seven datasets were agreed, and the user has already stubbed all seven files
under data/ (mostly {"place": "holder"} placeholders; runpod_gpu_skus.json
already has real RunPod pricing data seeded in; models_mflux.json still has
its real 35-entry catalog, unchanged):

```
┌─────┬──────────────────┬─────────────────────────────┬──────────────────────────────────────────┬─────────┬─────────────────────────────────────────────────────────────────┐
│  #  │     Dataset      │         Local path          │                 HF repo                  │ Public? │                          How populated                          │
├─────┼──────────────────┼─────────────────────────────┼──────────────────────────────────────────┼─────────┼─────────────────────────────────────────────────────────────────┤        │ 1   │ models_mflux     │ data/models_mflux.json      │ mflux-community/ci (upstream, not ours)  │ n/a     │ pull-only, hash-checked                                         │
├─────┼──────────────────┼─────────────────────────────┼──────────────────────────────────────────┼─────────┼─────────────────────────────────────────────────────────────────┤
│ 2   │ models_hf        │ data/models_hf.json         │ cleverheart2026/mflux-orchestrator-state │ public  │ we generate (org scan) → push                                   │
├─────┼──────────────────┼─────────────────────────────┼──────────────────────────────────────────┼─────────┼─────────────────────────────────────────────────────────────────┤
│ 3   │ models_missing   │ data/models_missing.json    │ same as #2                               │ public  │ we generate (diff) → push                                       │
├─────┼──────────────────┼─────────────────────────────┼──────────────────────────────────────────┼─────────┼─────────────────────────────────────────────────────────────────┤
│ 4   │ models_queue     │ data/models_queue.json      │ cleverheart2026/mflux-orchestrator-queue │ private │ human-authored → pull/push                                      │
├─────┼──────────────────┼─────────────────────────────┼──────────────────────────────────────────┼─────────┼─────────────────────────────────────────────────────────────────┤
│ 5   │ logs/devops      │ data/logs/devops.jsonl      │ same as #2                               │ public  │ append-only run/volume/poll events                              │
├─────┼──────────────────┼─────────────────────────────┼──────────────────────────────────────────┼─────────┼─────────────────────────────────────────────────────────────────┤
│ 6   │ logs/conversions │ data/logs/conversions.jsonl │ same as #2                               │ public  │ append-only per-quant build events, correlated to #5 via run_id │
├─────┼──────────────────┼─────────────────────────────┼──────────────────────────────────────────┼─────────┼─────────────────────────────────────────────────────────────────┤
│ 7   │ runpod_gpu_skus  │ data/runpod_gpu_skus.json   │ same as #2                               │ public  │ we generate (RunPod GPU-type API) → push                        │
└─────┴──────────────────┴─────────────────────────────┴──────────────────────────────────────────┴─────────┴─────────────────────────────────────────────────────────────────┘
```

Change detection for #1 was verified live this session: a plain HEAD against
https://huggingface.co/buckets/mflux-community/ci/resolve/models_mflux.json
returns x-xet-hash (a real content hash) without downloading anything —