# Osprey application image: query API + web UI + indexer worker in one
# image (selected by command). Multi-arch (arm64/amd64).
#
# Includes node + the SCIP indexers because in compose mode the worker runs
# them via the local executor — the worker container itself is the sandbox
# boundary (see the hardening in docker-compose.yml). The per-stage
# `--network=none` container sandbox remains available on bare-metal
# deployments with docker access.

FROM node:20-slim AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npx vite build

FROM python:3.13-slim
# node runtime + npm, pinned by copying from the node image
COPY --from=node:20-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g --ignore-scripts \
         @sourcegraph/scip-python@0.6.6 \
         @sourcegraph/scip-typescript@0.4.0 \
    && npm cache clean --force

WORKDIR /app
COPY pyproject.toml ./
COPY osprey/ osprey/
RUN pip install --no-cache-dir .

COPY --from=webbuild /web/dist /app/web/dist
ENV OSPREY_UI_DIST=/app/web/dist

RUN useradd -m osprey
USER osprey
EXPOSE 8800
CMD ["osprey", "api", "--host", "0.0.0.0", "--port", "8800"]
