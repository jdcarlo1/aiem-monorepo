import { useUser } from "@clerk/react";
import { getSessionId } from "@/lib/session";

export function useSessionId(): string {
  const { user, isLoaded } = useUser();
  if (isLoaded && user?.id) return user.id;
  return getSessionId();
}
