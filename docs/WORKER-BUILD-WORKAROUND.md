# Worker Build Workaround

## Slow Docker Pulls
Earlier today we had a lot of problems where RunPod was taking a long time to pull the docker container from GHCR.

I tested a workaround where, instead of pulling the whole ~6GB container, we instantiated a RunPod serverless worker using a repo instead of a Docker container.

The sample repo is [https://github.com/ianscrivener/runpod-hello-world](https://github.com/ianscrivener/runpod-hello-world)

