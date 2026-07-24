import "@testing-library/jest-dom";
import { afterEach, beforeEach, vi } from "vitest";

// Detect whether we are running in a DOM (jsdom) environment or a pure Node environment.
// api-smoke.test.ts runs with // @vitest-environment node and must not touch sessionStorage/window.
const HAS_DOM =
  typeof window !== "undefined" && typeof sessionStorage !== "undefined";

const STUB_LOCATION = {
  href: "",
  assign: vi.fn(),
  replace: vi.fn(),
  reload: vi.fn(),
  pathname: "/",
  search: "",
  hash: "",
  origin: "http://localhost",
  host: "localhost",
  hostname: "localhost",
  protocol: "http:",
  port: "",
};

beforeEach(() => {
  if (!HAS_DOM) return;
  sessionStorage.clear();
  vi.stubGlobal("location", { ...STUB_LOCATION });
});

afterEach(async () => {
  if (HAS_DOM) {
    const { cleanup } = await import("@testing-library/react");
    cleanup();
    vi.unstubAllGlobals();
  }
});
