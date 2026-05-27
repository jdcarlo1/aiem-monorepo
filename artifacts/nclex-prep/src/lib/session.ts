export function getSessionId(): string {
  let sessionId = localStorage.getItem("nclex_session_id");
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    localStorage.setItem("nclex_session_id", sessionId);
  }
  return sessionId;
}

export function getPaymentEmail(): string | null {
  return localStorage.getItem("nclex_payment_email");
}

export function setPaymentEmail(email: string): void {
  localStorage.setItem("nclex_payment_email", email);
}
