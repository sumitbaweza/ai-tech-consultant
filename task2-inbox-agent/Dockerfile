# The Inbox Agent's image, shared by inbox-migrate and inbox.
FROM node:22-slim

# pnpm at the version package.json names, installed with npm so no corepack is needed
RUN npm install -g pnpm@10.34.9

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# Dependencies first: Docker reuses this layer until package.json or the lockfile change.
# Dev dependencies stay: inbox-migrate runs TypeScript through tsx.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# Run as the unprivileged user the Node image ships with. Next.js writes its cache in .next.
RUN chown -R node:node .next
USER node

EXPOSE 3000
CMD ["pnpm", "start"]
