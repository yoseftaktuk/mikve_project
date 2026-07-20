FROM node:20-alpine AS build

WORKDIR /app

# Pi builds often hit ETIMEDOUT on large npm downloads; tune retries and concurrency.
ENV NPM_CONFIG_FETCH_RETRIES=10 \
    NPM_CONFIG_FETCH_RETRY_MINTIMEOUT=20000 \
    NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=120000 \
    NPM_CONFIG_FETCH_TIMEOUT=600000 \
    NPM_CONFIG_MAXSOCKETS=2

COPY package.json package-lock.json /app/
# npm ci can silently skip platform-specific optional native deps
# (npm/cli#4828), leaving @rolldown/binding-*/*.node missing on arm64/musl.
# Detect that case and self-heal with a clean install.
RUN --mount=type=cache,target=/root/.npm \
    set -e; \
    attempt=1; \
    while [ "$attempt" -le 3 ]; do \
      if npm ci; then break; fi; \
      if [ "$attempt" -eq 3 ]; then exit 1; fi; \
      attempt=$((attempt + 1)); \
    done; \
    if [ -z "$(find node_modules/@rolldown -name '*.node' 2>/dev/null)" ]; then \
      echo "[dockerfile] rolldown native binding missing after npm ci; doing clean install"; \
      rm -rf node_modules package-lock.json; \
      npm install; \
    fi
COPY . /app
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
