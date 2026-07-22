import { useEffect, useRef, useCallback } from "react";
import { getToken } from "@/lib/auth";

export interface SseEvent {
  category: string;
  data: unknown;
  id?: string;
}

export type SseHandler = (evt: SseEvent) => void;

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30_000;
const JITTER_MS = 500;

function buildUrl(): string {
  const base = "/stock-api/events/stream";
  const token = getToken();
  if (token) {
    return `${base}?token=${encodeURIComponent(token)}`;
  }
  return base;
}

export function useEventStream(
  categories: string[] | null,
  onEvent: SseHandler,
  enabled: boolean = true,
): { connected: boolean } {
  const esRef = useRef<EventSource | null>(null);
  const lastIdRef = useRef<string>("");
  const delayRef = useRef<number>(BASE_DELAY_MS);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectedRef = useRef(false);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    let url = buildUrl();
    if (lastIdRef.current) {
      url += (url.includes("?") ? "&" : "?") + `lastEventId=${encodeURIComponent(lastIdRef.current)}`;
    }
    if (categories && categories.length > 0) {
      url += (url.includes("?") ? "&" : "?") + `categories=${encodeURIComponent(categories.join(","))}`;
    }

    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    es.onopen = () => {
      connectedRef.current = true;
      delayRef.current = BASE_DELAY_MS;
    };

    es.onmessage = (raw) => {
      if (raw.data === "heartbeat" || raw.data === "") return;
      if (raw.lastEventId) lastIdRef.current = raw.lastEventId;
      try {
        const parsed = JSON.parse(raw.data) as SseEvent;
        handlerRef.current(parsed);
      } catch {
        // ignore malformed frames
      }
    };

    es.addEventListener("heartbeat", () => {
      connectedRef.current = true;
    });

    es.onerror = () => {
      connectedRef.current = false;
      es.close();
      esRef.current = null;
      const jitter = Math.random() * JITTER_MS;
      timerRef.current = setTimeout(() => {
        delayRef.current = Math.min(delayRef.current * 2, MAX_DELAY_MS);
        connect();
      }, delayRef.current + jitter);
    };
  }, [categories]);

  useEffect(() => {
    if (!enabled) return;
    connect();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      connectedRef.current = false;
    };
  }, [enabled, connect]);

  return { connected: connectedRef.current };
}
