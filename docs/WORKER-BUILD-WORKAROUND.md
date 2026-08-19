# Worker build work around. 


## # Slow Docker Pulls
Earlier today we had a lot of problems where run pod was taking a long time to pull the docker container from GHCR 

I tested a workaround where instead of pulling the whole container around about 6 gig, we actually instantiated a run pod serverless worker using a repo instead of the, instead of a Docker container. 

The sample repo is [https://github.com/ianscrivener/runpod-hello-world](https://github.com/ianscrivener/runpod-hello-world)

