FROM node:20-alpine AS build

WORKDIR /app

# Pi builds often hit ETIMEDOUT on large npm downloads; tune retries and concurrency.
ENV NPM_CONFIG_FETCH_RETRIES=10 \
    NPM_CONFIG_FETCH_RETRY_MINTIMEOUT=20000 \
    NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=120000 \
    NPM_CONFIG_FETCH_TIMEOUT=600000 \
    NPM_CONFIG_MAXSOCKETS=2

# #region agent log
RUN node -e "require('dns').lookup('registry.npmjs.org', (err, addr) => { if (err) { console.error('[agent-debug] dns_status=FAIL host=registry.npmjs.org', err.message); console.error('[agent-debug] fix_hint=Set Docker Desktop DNS to your router IP (see deploy/docker-daemon.dns.example.json)'); process.exit(1); } console.log('[agent-debug] dns_status=OK registry.npmjs.org ->', addr); })"
# #endregion
COPY package.json package-lock.json /app/
# npm ci can silently skip platform-specific optional native deps
# (npm/cli#4828), leaving @rolldown/binding-*/*.node missing on arm64/musl.
# Detect that case and self-heal with a clean install.
# #region agent log
RUN --mount=type=cache,target=/root/.npm \
    set -e; \
    attempt=1; \
    while [ "$attempt" -le 3 ]; do \
      echo "[agent-debug] npm_ci_attempt=$attempt retries=$NPM_CONFIG_FETCH_RETRIES timeout_ms=$NPM_CONFIG_FETCH_TIMEOUT maxsockets=$NPM_CONFIG_MAXSOCKETS"; \
      if npm ci; then echo "[agent-debug] npm_ci_status=OK attempt=$attempt"; break; fi; \
      if [ "$attempt" -eq 3 ]; then echo "[agent-debug] npm_ci_status=FAIL attempts=3"; exit 1; fi; \
      echo "[agent-debug] npm_ci_status=RETRY attempt=$attempt"; \
      attempt=$((attempt + 1)); \
    done; \
    if [ -z "$(find node_modules/@rolldown -name '*.node' 2>/dev/null)" ]; then \
      echo "[dockerfile] rolldown native binding missing after npm ci; doing clean install"; \
      rm -rf node_modules package-lock.json; \
      npm install; \
    fi
# #endregion
# #region agent log
RUN echo "[agent-debug] npm=$(npm -v) node=$(node -v) arch=$(node -p 'process.arch')"; \
    echo "[agent-debug] rolldown .node files:"; \
    find node_modules/@rolldown -name '*.node' -print 2>/dev/null || echo "[agent-debug] NONE"
# #endregion
COPY . /app
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
