import { describe, it, expect, beforeEach } from "vitest";
import {
  getToken,
  setToken,
  clearToken,
  getCsrfToken,
  setCsrfToken,
} from "@/lib/auth";

const SESSION_KEY = "aiem_admin_token";
const CSRF_KEY = "aiem_csrf_token";

describe("auth utilities", () => {
  beforeEach(() => sessionStorage.clear());

  describe("getToken / setToken", () => {
    it("returns null when no token is stored", () => {
      expect(getToken()).toBeNull();
    });

    it("returns the stored token after setToken", () => {
      setToken("abc-123");
      expect(getToken()).toBe("abc-123");
    });

    it("overwrites a previous token on setToken", () => {
      setToken("first");
      setToken("second");
      expect(getToken()).toBe("second");
    });
  });

  describe("clearToken", () => {
    it("removes the admin token from sessionStorage", () => {
      setToken("tok");
      clearToken();
      expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
    });

    it("removes the CSRF token from sessionStorage", () => {
      setCsrfToken("csrf-xyz");
      clearToken();
      expect(sessionStorage.getItem(CSRF_KEY)).toBeNull();
    });

    it("is safe to call when nothing is stored", () => {
      expect(() => clearToken()).not.toThrow();
    });
  });

  describe("getCsrfToken / setCsrfToken", () => {
    it("returns empty string when nothing is stored", () => {
      expect(getCsrfToken()).toBe("");
    });

    it("returns the stored CSRF token after setCsrfToken", () => {
      setCsrfToken("csrf-abc");
      expect(getCsrfToken()).toBe("csrf-abc");
    });

    it("reads CSRF token from sessionStorage priority over cookie", () => {
      setCsrfToken("from-session");
      Object.defineProperty(document, "cookie", {
        writable: true,
        value: "aiem_csrf=from-cookie",
      });
      expect(getCsrfToken()).toBe("from-session");
    });
  });
});
