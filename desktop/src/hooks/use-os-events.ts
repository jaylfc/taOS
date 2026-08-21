import { useEffect, useState, useCallback, useRef } from "react";

export type OsEvent = {
  kind: string;
  /** Trace id of the source event. Null on control frames, which describe the
   *  stream itself rather than a change that happened in it. */
  id: string | null;
  ts: number;
  /** Only on `events.lagged`: how many events were dropped before this frame. */
  dropped?: number;
};

/** Control frame: the server dropped events for this connection because the
 *  client fell behind. It is NOT a change notification, so it bypasses the
 *  caller's kind filter — a subscriber that asked for one kind still has to
 *  learn it may have missed some of that kind. */
export const LAGGED_KIND = "events.lagged";

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

    const alreadySeen = (id: string | null | undefined): boolean => {
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
      if (event.kind === LAGGED_KIND) {
        // Always delivered, and never deduped: its id is null, so every lag
        // frame after the first would otherwise collapse into "already seen".
        onEventRef.current(event);
        return;
      }
      if (kindsRef.current.length > 0 && !kindsRef.current.includes(event.kind)) {
        return;
      }
      if (alreadySeen(event.id)) return;
      onEventRef.current(event);
    };

    es.onerror = () => {
      if (stoppedRef.current) return;
      // Whatever the readyState, an error means we are not receiving events.
      // Only a CLOSED stream is ours to reconnect: while CONNECTING the
      // browser is already retrying, and scheduling our own reconnect on top
      // of that would open a second stream.
      setConnected(false);
      setStale(true);
      if (es.readyState === EventSource.CLOSED) {
        const delay = Math.min(
          RECONNECT_DELAY_MS * 2 ** reconnectAttemptsRef.current,
          MAX_RECONNECT_DELAY_MS,
        );
        reconnectAttemptsRef.current += 1;
        reconnectTimerRef.current = setTimeout(() => {
          if (!stoppedRef.current) connect();
        }, delay);
      }
    };
  }, []);

  // Reconnect when the caller's kinds change: the URL is built once per
  // connection, so without this a widened kinds list keeps streaming the old
  // server-side filter and the new kinds never arrive.
  const kindsKey = kinds.join(",");

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
  }, [connect, kindsKey]);

  return { connected, stale };
}
