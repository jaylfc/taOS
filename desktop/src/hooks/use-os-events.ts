import { useEffect, useState, useCallback, useRef } from "react";

export type OsEvent = {
  kind: string;
  id: string;
  ts: number;
};

type OsEventHandler = (event: OsEvent) => void;

const RECONNECT_DELAY_MS = 5000;
const MAX_RECONNECT_DELAY_MS = 30000;
const MAX_SEEN_IDS = 128;

export function useOsEvents(kinds: string[], onEvent: OsEventHandler): {
  connected: boolean;
  stale: boolean;
} {
  const [connected, setConnected] = useState(false);
  const [stale, setStale] = useState(true);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const stoppedRef = useRef(false);
  const esRef = useRef<EventSource | null>(null);
  const seenIdsRef = useRef<string[]>([]);
  const seenRef = useRef(new Set<string>());
  const onEventRef = useRef(onEvent);
  const kindsRef = useRef(kinds);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    kindsRef.current = kinds;
  }, [kinds]);

  const connect = useCallback(() => {
    if (stoppedRef.current) return;

    const kindsParam = kindsRef.current.join(",");
    const url = kindsParam
      ? `/api/os/events?kinds=${encodeURIComponent(kindsParam)}`
      : "/api/os/events";

    const es = new EventSource(url);
    esRef.current = es;

    const alreadySeen = (id: string | undefined): boolean => {
      if (!id) return false;
      if (seenRef.current.has(id)) return true;
      seenRef.current.add(id);
      seenIdsRef.current.push(id);
      if (seenIdsRef.current.length > MAX_SEEN_IDS) {
        const oldest = seenIdsRef.current.shift();
        if (oldest) seenRef.current.delete(oldest);
      }
      return false;
    };

    es.onopen = () => {
      reconnectAttemptsRef.current = 0;
      setConnected(true);
      setStale(false);
    };

    es.onmessage = (msg) => {
      let event: OsEvent | null;
      try {
        event = JSON.parse(msg.data as string) as OsEvent;
      } catch {
        return;
      }
      if (!event || typeof event !== "object") return;
      if (!event.kind) return;
      if (kindsRef.current.length > 0 && !kindsRef.current.includes(event.kind)) {
        return;
      }
      if (alreadySeen(event.id)) return;
      onEventRef.current(event);
    };

    es.onerror = () => {
      if (!stoppedRef.current && es.readyState === EventSource.CLOSED) {
        const delay = Math.min(
          RECONNECT_DELAY_MS * 2 ** reconnectAttemptsRef.current,
          MAX_RECONNECT_DELAY_MS,
        );
        reconnectAttemptsRef.current += 1;
        setConnected(false);
        setStale(true);
        reconnectTimerRef.current = setTimeout(() => {
          if (!stoppedRef.current) connect();
        }, delay);
      }
    };
  }, []);

  useEffect(() => {
    stoppedRef.current = false;
    connect();

    return () => {
      stoppedRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      esRef.current?.close();
      setConnected(false);
      setStale(true);
    };
  }, [connect]);

  return { connected, stale };
}
