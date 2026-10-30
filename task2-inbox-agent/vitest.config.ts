import { resolve } from "node:path";
import { defineConfig } from "vitest/config";
import { loadRootEnv } from "./src/root-env";

// Same root .env as the app, so database tests find the inbox password
loadRootEnv();

export default defineConfig({
  resolve: { alias: { "@": resolve(import.meta.dirname, "src") } },
  test: { include: ["tests/**/*.test.ts"], environment: "node" },
});
