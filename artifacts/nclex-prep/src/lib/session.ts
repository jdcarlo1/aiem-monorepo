export function getSessionId(): string {
  let sessionId = localStorage.getItem("nclex_session_id");
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    localStorage.setItem("nclex_session_id", sessionId);
  }
  return sessionId;
}
