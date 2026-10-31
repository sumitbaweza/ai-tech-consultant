import type { NextConfig } from "next";
import { loadRootEnv } from "./src/root-env";

loadRootEnv();

const nextConfig: NextConfig = {
  // Links to a route that doesn't exist fail the type check
  typedRoutes: true,
  // Don't write an AGENTS.md into the project on `next dev`
  agentRules: false,
};

export default nextConfig;
