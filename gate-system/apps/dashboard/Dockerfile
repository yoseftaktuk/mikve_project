FROM node:20-alpine AS build

WORKDIR /app
COPY package.json package-lock.json /app/
# npm ci can silently skip platform-specific optional native deps
# (npm/cli#4828), leaving @rolldown/binding-*/*.node missing on arm64/musl.
# Detect that case and self-heal with a clean install.
RUN npm ci \
 && if [ -z "$(find node_modules/@rolldown -name '*.node' 2>/dev/null)" ]; then \
      echo "[dockerfile] rolldown native binding missing after npm ci; doing clean install"; \
      rm -rf node_modules package-lock.json && npm install; \
    fi
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
