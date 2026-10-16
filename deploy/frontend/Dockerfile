# Copy of frontend/Dockerfile plus the NEXT_PUBLIC_API_BASE_URL build arg (design 2.3),
# so a production build bakes the relative API base and not the localhost fallback.
# OPS-3 puts the arg into frontend/Dockerfile itself; delete this file then and
# drop `dockerfile:` from the frontend service in docker-compose.prod.yml.
# Multi-stage build for the Next.js SPA.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# The lockfile is committed, so install from it. `npm ci` fails on any drift
# between package.json and package-lock.json and resolves nothing fresh — the
# image installs exactly the dependency tree the frontend gates tested.
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_API_BASE_URL=/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build

FROM node:22-alpine AS runner
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
