# Security Audit Tasks

1. Review and reconsider architecture for `data/models_queue.json` ~ `https://huggingface.co/buckets/mflux-community/ci/resolve/models_queue.json?download=true`. Consider having the HF version of this file as a throwaway duplicate with the master version of this file in another secured location (DO, private HF Buclket or other). 

