export function getToken(): string | null {
  return sessionStorage.getItem("aiem_admin_token");
}

export function setToken(token: string): void {
  sessionStorage.setItem("aiem_admin_token", token);
}

export function clearToken(): void {
  sessionStorage.removeItem("aiem_admin_token");
}
