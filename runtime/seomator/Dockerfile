FROM mcr.microsoft.com/playwright:v1.57.0-noble@sha256:3bed4b1a12f2338642f3d8cba28e291deef3c66bd4a964bbeb3e57bbff511dbd

WORKDIR /app
COPY package.json package-lock.json extella_normalize_package.mjs ./
RUN node extella_normalize_package.mjs \
    && npm ci --no-audit --no-fund
COPY . ./
COPY extella_safe_fetch.mjs /app/extella_safe_fetch.mjs
COPY extella_browser_route.mjs /app/extella_browser_route.mjs
RUN npm run build \
    && npm prune --omit=dev --no-audit --no-fund

ENV NODE_ENV=production \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    EXTELLA_SAFE_FETCH_MODULE=/app/extella_safe_fetch.mjs \
    EXTELLA_WORKER_KIND=SEOmator \
    EXTELLA_WORKER_PORT=8082 \
    EXTELLA_SAFE_FETCH_PRELOAD=1 \
    NODE_OPTIONS=--import=/app/extella_safe_fetch.mjs
EXPOSE 8082
ENTRYPOINT ["node", "/app/extella_entrypoint.mjs"]
