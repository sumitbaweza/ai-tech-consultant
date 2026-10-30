import nextVitals from "eslint-config-next/core-web-vitals";
import { defineConfig, globalIgnores } from "eslint/config";
import tseslint from "typescript-eslint";

export default defineConfig([
  // Next.js and React rules: hooks, a11y, <Link> and <Image> use
  ...nextVitals,
  // The strictest typescript-eslint preset, which reads types (no floating promises, no any)
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
  },
  // Plain JavaScript config files have no types to check
  { files: ["**/*.mjs"], extends: [tseslint.configs.disableTypeChecked] },
  globalIgnores([".next/**", "node_modules/**", "next-env.d.ts", "drizzle/**"]),
]);
