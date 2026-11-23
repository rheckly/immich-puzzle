# Image ghcr.io/rheckly/immich-puzzle-render: renders shared puzzles as MP4 (see render/README.md).
# Build from the repository root (it needs app/puzzle-engine.js and app/fonts):
#   docker build -f render/Dockerfile -t immich-puzzle-render .
# No browser: the same puzzle engine runs in Node with Skia (@napi-rs/canvas), ffmpeg writes H.264/AAC.
FROM node:22-alpine
ENV NODE_ENV=production TZ=UTC
RUN apk add --no-cache ffmpeg ttf-dejavu
WORKDIR /app
COPY render/package.json render/package-lock.json ./
# Exact versions with checksums from package-lock.json, no install scripts
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY render/render.js ./
COPY app/puzzle-engine.js engine/
COPY app/fonts/ engine/fonts/
USER node
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s CMD ["node", "render.js", "--health"]
CMD ["node", "render.js"]
