import { existsSync } from "node:fs";
import { resolve } from "node:path";

/** All three tasks share one .env at the repo root, but Next.js only reads its own folder.
 *  Load the root file too. Variables already set (by Docker Compose, say) keep their values,
 *  and inside the Docker image, where there is no root .env, this does nothing. */
export function loadRootEnv(): void {
  const path = resolve(process.cwd(), "..", ".env");
  if (existsSync(path)) process.loadEnvFile(path);
}
