# Osprey indexer image: SCIP indexers pre-installed so the index stage can
# run with --network=none (ARCHITECTURE.md §11.1). Multi-arch (arm64/amd64).
FROM node:20-slim

RUN npm install -g --ignore-scripts \
      @sourcegraph/scip-python@0.6.6 \
      @sourcegraph/scip-typescript@0.4.0 \
    && npm cache clean --force

# git for repo tooling; python3 because scip-python resolves against a real
# interpreter/environment; non-root default
RUN apt-get update && apt-get install -y --no-install-recommends \
      git python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

USER node
WORKDIR /src
