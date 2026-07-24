import { renderHook, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useApi } from "@/hooks/use-api";

vi.mock("@/lib/auth", () => ({
  getToken: vi.fn(() => "test-admin-token"),
  clearToken: vi.fn(),
  getCsrfToken: vi.fn(() => ""),
}));

function mockFetch(response: Partial<Response> & { jsonBody?: unknown }) {
  const { jsonBody, ...rest } = response;
  const mockRes = {
    ok: true,
    status: 200,
    json: async () => jsonBody ?? {},
    ...rest,
  };
  global.fetch = vi.fn().mockResolvedValue(mockRes);
}

describe("useApi hook", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("starts with loading=true and data=null", async () => {
    mockFetch({ jsonBody: { status: "ok" } });
    const { result } = renderHook(() => useApi("/stock-api/health"));
    expect(result.current.loading).toBe(true);
    expect(result.current.data).toBeNull();
  });

  it("resolves data and sets loading=false on success", async () => {
    mockFetch({ jsonBody: { status: "ok", count: 5 } });
    const { result } = renderHook(() => useApi("/stock-api/health"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ status: "ok", count: 5 });
    expect(result.current.error).toBeNull();
  });

  it("sets error on non-2xx response", async () => {
    mockFetch({ ok: false, status: 500, jsonBody: {} });
    const { result } = renderHook(() => useApi("/stock-api/health"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toContain("500");
  });

  it("sends X-Admin-Token header when token is available", async () => {
    mockFetch({ jsonBody: {} });
    const { result } = renderHook(() => useApi("/stock-api/admin/health"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    const [, fetchOptions] = (global.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(fetchOptions.headers["X-Admin-Token"]).toBe("test-admin-token");
  });

  it("calls clearToken and sets redirect href on 401", async () => {
    const { clearToken } = await import("@/lib/auth");
    mockFetch({ ok: false, status: 401, json: async () => ({}) });

    const { result } = renderHook(() => useApi("/stock-api/admin/data"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(clearToken).toHaveBeenCalled();
    expect(window.location.href).toBe("/aiem/");
  });

  it("calls clearToken and sets redirect href on 403", async () => {
    const { clearToken } = await import("@/lib/auth");
    vi.mocked(clearToken).mockClear();
    mockFetch({ ok: false, status: 403, json: async () => ({}) });

    const { result } = renderHook(() => useApi("/stock-api/admin/data"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(clearToken).toHaveBeenCalled();
    expect(window.location.href).toBe("/aiem/");
  });

  it("sets up polling and clears interval on unmount", async () => {
    mockFetch({ jsonBody: { data: [] } });
    const clearIntervalSpy = vi.spyOn(global, "clearInterval");

    const { unmount } = renderHook(() =>
      useApi("/stock-api/health", {}, 30_000)
    );
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));

    unmount();
    expect(clearIntervalSpy).toHaveBeenCalled();
  });

  it("does not set interval when pollIntervalMs is not provided", async () => {
    mockFetch({ jsonBody: {} });
    const setIntervalSpy = vi.spyOn(global, "setInterval");

    renderHook(() => useApi("/stock-api/health"));
    await waitFor(() =>
      expect((global.fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0)
    );

    // Filter out vitest's internal shouldAdvanceTime heartbeat (delay=50ms)
    const appCalls = setIntervalSpy.mock.calls.filter(
      ([, delay]) => (delay as number) !== 50
    );
    expect(appCalls).toHaveLength(0);
  });

  it("exposes a refetch function that re-fetches data", async () => {
    mockFetch({ jsonBody: { count: 1 } });
    const { result } = renderHook(() => useApi("/stock-api/health"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    mockFetch({ jsonBody: { count: 2 } });
    await act(async () => {
      await result.current.refetch();
    });

    expect(result.current.data).toEqual({ count: 2 });
  });

  it("exposes isStale boolean that starts false immediately after fetch", async () => {
    mockFetch({ jsonBody: {} });
    const { result } = renderHook(() => useApi("/stock-api/health", {}, 30_000));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.isStale).toBe(false);
  });
});
