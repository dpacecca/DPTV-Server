FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# Pinned to the 1.31 line, at 1.31.9 or later: 1.31.9 fixes the NGINX2Root flaws (CVE-2026-123007 and
# four more). The check fails the build if a stale cached image is older, so build with --pull.
FROM nginx:1.31-alpine
RUN v="$(nginx -v 2>&1 | sed -n 's|.*nginx/\([0-9.]*\).*|\1|p')"; \
    echo "$v" | awk -F. '{ exit !($1 > 1 || ($1 == 1 && ($2 > 31 || ($2 == 31 && $3 >= 9)))) }' \
    || { echo "nginx $v is older than 1.31.9 (NGINX2Root); rebuild with --pull" >&2; exit 1; }
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
