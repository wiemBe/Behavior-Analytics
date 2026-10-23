# Multi-stage build for the Next.js SPA. The base is pinned by digest in every
# stage (docs/DEVELOPMENT.md "Base image pins"); the tag stays for readability.
FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# The lockfile is committed, so install from it. `npm ci` fails on any drift
# between package.json and package-lock.json and resolves nothing fresh — the
# image installs exactly the dependency tree the frontend gates tested.
RUN npm ci

FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# NEXT_PUBLIC_* is inlined at `next build`, so these are build args and not
# runtime settings. The default is the relative API base, which is same-origin
# behind the bundled proxy; a development build passes the split-port URL.
ARG NEXT_PUBLIC_API_BASE_URL=/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ARG APP_VERSION
ENV NEXT_PUBLIC_APP_VERSION=$APP_VERSION
RUN npm run build

FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS runner
ARG APP_VERSION
ARG GIT_SHA
LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_SHA
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
# The standalone server binds to $HOSTNAME, which Docker sets to the container
# ID. That is not resolvable from inside the container, so startup dies with
# `getaddrinfo EAI_AGAIN <container-id>`. Bind all interfaces instead — the
# published port is already pinned to 127.0.0.1 in docker-compose.yml.
ENV HOSTNAME=0.0.0.0 PORT=3000
RUN addgroup -g 1001 nodejs && adduser -u 1001 -G nodejs -S nextjs
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
