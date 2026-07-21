import { useState, useEffect, useCallback } from "react";
import { getToken, clearToken } from "@/lib/auth";

export interface UseApiResponse<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  isStale: boolean;
  lastUpdated: Date | null;
  refetch: () => Promise<void>;
}

export function useApi<T>(
  url: string,
  options?: RequestInit,
  pollIntervalMs?: number
): UseApiResponse<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);
  const [lastFetched, setLastFetched] = useState<number | null>(null);

  const fetchApi = useCallback(async () => {
    try {
      const token = getToken();
      const headers: Record<string, string> = {
        ...(options?.headers as Record<string, string>),
      };

      if (url.includes('/admin/')) {
        if (!token) {
          window.location.href = "/";
          return;
        }
        headers["X-Admin-Token"] = token;
      }

      const res = await fetch(url, { ...options, headers });

      if (res.status === 403 || res.status === 401) {
        clearToken();
        window.location.href = "/";
        return;
      }

      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);

      const json = await res.json();
      setData(json);
      setLastFetched(Date.now());
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [url, options]);

  useEffect(() => {
    fetchApi();
    if (pollIntervalMs) {
      const interval = setInterval(fetchApi, pollIntervalMs);
      return () => clearInterval(interval);
    }
    return undefined;
  }, [fetchApi, pollIntervalMs]);

  const isStale = pollIntervalMs && lastFetched
    ? Date.now() - lastFetched > pollIntervalMs * 2
    : false;

  const lastUpdated = lastFetched ? new Date(lastFetched) : null;

  return { data, loading, error, isStale, lastUpdated, refetch: fetchApi };
}
