# The review UI: Next.js production build. The API address is fixed at build
# time (NEXT_PUBLIC_*), as the browser calls the API directly.
FROM node:22.21.1-bookworm-slim AS build
WORKDIR /ui
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:22.21.1-bookworm-slim
WORKDIR /ui
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /ui /ui
USER node
EXPOSE 3000
CMD ["node_modules/.bin/next", "start", "-H", "0.0.0.0", "-p", "3000"]
