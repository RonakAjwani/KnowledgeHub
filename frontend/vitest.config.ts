/**
 * Vitest, scoped to the logic worth testing on the frontend.
 *
 * The alias is declared here rather than pulled in through a tsconfig-paths
 * plugin: there is exactly one alias (`@/*` -> the project root, per
 * tsconfig.json), and an extra dependency to resolve a single mapping is a
 * dependency that can break the test run for no benefit.
 *
 * `environment: "jsdom"` because the SSE reader, the chat reducer and the
 * citation chip all touch DOM or React APIs. The tests themselves avoid
 * rendering whole pages - they drive the units that carry the contract.
 */

import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // `node_modules` and the Next build output hold their own test files;
    // running them would test other people's libraries.
    include: ["**/*.test.ts", "**/*.test.tsx"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
