FROM node:24.19.0-alpine3.24@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43

WORKDIR /runner
COPY runner/package.json runner/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY . ./
COPY extella_safe_fetch.mjs /app/extella_safe_fetch.mjs
RUN node node_modules/prisma/build/index.js generate

ENV NODE_ENV=production \
    TSX_CLI=/runner/node_modules/tsx/dist/cli.mjs \
    EXTELLA_WORKER_KIND=CrawlSEO \
    EXTELLA_WORKER_PORT=8081 \
    EXTELLA_SAFE_FETCH_PRELOAD=1 \
    NODE_OPTIONS=--import=/app/extella_safe_fetch.mjs
EXPOSE 8081
ENTRYPOINT ["node", "/app/extella_entrypoint.mjs"]
