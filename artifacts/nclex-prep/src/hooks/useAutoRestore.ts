import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getPaymentEmail } from "@/lib/session";
import { getGetSessionStatusQueryKey } from "@workspace/api-client-react";

export function useAutoRestore(sessionId: string, canAnswerMore: boolean | undefined) {
  const queryClient = useQueryClient();
  const attempted = useRef(false);

  useEffect(() => {
    if (canAnswerMore !== false) return;
    if (attempted.current) return;

    const email = getPaymentEmail();
    if (!email || !sessionId) return;

    attempted.current = true;

    fetch("/api/stripe/restore-access", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, email }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.success) {
          queryClient.invalidateQueries({
            queryKey: getGetSessionStatusQueryKey({ sessionId }),
          });
        }
      })
      .catch(() => {});
  }, [canAnswerMore, sessionId, queryClient]);
}

export function useEagerRestore(sessionId: string, isSubscribed: boolean | undefined) {
  const queryClient = useQueryClient();
  const attempted = useRef(false);

  useEffect(() => {
    if (isSubscribed) return;
    if (attempted.current) return;
    if (!sessionId) return;

    const email = getPaymentEmail();
    if (!email) return;

    attempted.current = true;

    fetch("/api/stripe/restore-access", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, email }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.success) {
          queryClient.invalidateQueries({
            queryKey: getGetSessionStatusQueryKey({ sessionId }),
          });
        }
      })
      .catch(() => {});
  }, [isSubscribed, sessionId, queryClient]);
}
