# Multi-stage frontend build: node for the bundle, nginx:alpine to serve it
# (REQUIREMENTS INF-002/INF-005; DECISIONS D-059). No secrets in either stage.

# --- Stage 1: build ------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- Stage 2: serve ------------------------------------------------------------
# 1.31.4-alpine (alpine 3.24, not the older 1.27-alpine/3.21) + an explicit
# apk upgrade: Trivy fail-closed found CRITICAL/HIGH CVEs in the stale
# openssl/expat/c-ares shipped with the old base (CI run 33385873103).
FROM nginxinc/nginx-unprivileged:1.31.4-alpine AS runtime
USER root
RUN apk update && apk upgrade --no-cache && rm -rf /var/cache/apk/*
USER nginx
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
