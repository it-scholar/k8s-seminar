# ── Build stage ────────────────────────────────────────────────────────────────
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM node:22-alpine

WORKDIR /app

# Create non-root user
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

# adapter-node bundles everything into build/ — no node_modules needed at runtime
COPY --from=build --chown=appuser:appgroup /app/build ./build

USER appuser

EXPOSE 3000

ENV NODE_ENV=production
ENV PORT=3000
# ORIGIN must be set at runtime to your public URL, e.g. https://jokes.example.com
# ENV ORIGIN=https://jokes.example.com

CMD ["node", "build/index.js"]
