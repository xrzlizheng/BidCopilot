import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    // Run the whole suite in a single forked process. For a suite this small the
    // serial cost is negligible, and it keeps runs deterministic. It also avoids
    // concurrent writes to vitest's on-disk transform cache: under the WorkBuddy
    // sandbox's brokered-fs shim those concurrent writes intermittently fail with
    // EPERM, which both aborts collection of some test files and makes
    // `vitest run` exit non-zero even though every test passes.
    pool: "forks",
    poolOptions: {
      forks: { singleFork: true },
    },
  },
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
