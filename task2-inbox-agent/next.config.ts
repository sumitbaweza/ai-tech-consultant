import type { NextConfig } from "next";
import { loadRootEnv } from "./src/root-env";

loadRootEnv();

const nextConfig: NextConfig = {
  // Links to a route that doesn't exist fail the type check
  typedRoutes: true,
};

export default nextConfig;
