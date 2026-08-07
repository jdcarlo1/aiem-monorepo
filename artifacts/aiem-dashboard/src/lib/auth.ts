const SESSION_KEY = "aiem_admin_token";
const CSRF_KEY = "aiem_csrf_token";
const ROLE_KEY = "aiem_role";

/** Commercial roles model (documented; Admin token is what is enforced today). */
export type AiemRole = "Viewer" | "Trader" | "Auditor" | "Admin";

export function getRole(): AiemRole {
  const raw = (sessionStorage.getItem(ROLE_KEY) || "Admin").trim();
  if (raw === "Viewer" || raw === "Trader" || raw === "Auditor" || raw === "Admin") {
    return raw;
  }
  return "Admin";
}

export function setRole(role: AiemRole): void {
  sessionStorage.setItem(ROLE_KEY, role);
}

export function getToken(): string | null {
  return sessionStorage.getItem(SESSION_KEY);
}

export function setToken(token: string): void {
  sessionStorage.setItem(SESSION_KEY, token);
}

export function clearToken(): void {
  sessionStorage.removeItem(SESSION_KEY);
  sessionStorage.removeItem(CSRF_KEY);
  sessionStorage.removeItem(ROLE_KEY);
}

export function getCsrfToken(): string {
  const fromSession = sessionStorage.getItem(CSRF_KEY);
  if (fromSession) return fromSession;
  const match = document.cookie.match(/(?:^|;\s*)aiem_csrf=([^;]+)/);
  const fromCookie = match ? decodeURIComponent(match[1]) : "";
  if (fromCookie) sessionStorage.setItem(CSRF_KEY, fromCookie);
  return fromCookie;
}

export function setCsrfToken(token: string): void {
  sessionStorage.setItem(CSRF_KEY, token);
}

export async function serverLogout(): Promise<void> {
  try {
    await fetch("/stock-api/auth/logout", {
      method: "POST",
      credentials: "include",
      headers: getCsrfToken() ? { "X-CSRF-Token": getCsrfToken() } : {},
    });
  } catch {
    // ignore network errors on logout
  } finally {
    clearToken();
  }
}
