import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/__tests__/**/*.test.ts"],
    // 15 s per test — gives supertest/Express enough headroom under CI load
    // without masking real hangs (15 s is still far below any legitimate route time).
    testTimeout: 15000,
    coverage: {
      provider: "v8",
      reporter: ["text", "json"],
      include: ["src/**/*.ts"],
      exclude: ["src/__tests__/**"],
      all: true,
    },
  },
});
