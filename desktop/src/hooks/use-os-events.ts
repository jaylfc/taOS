import { useEffect, useState, useRef } from "react";

export type OsEvent = {
  kind: string;
  id: string | null;
  ts: number;
  dropped?: number;
};

export const LAGGED_KIND = "events.lagged";

type OsEventHandler = (event: OsEvent) => void;

const RECONNECT_DELAY_MS = 5000;
const MAX_RECONNECT_DELAY_MS = 30000;
const MAX_SEEN_IDS = 128;

type Subscriber = {
  kinds: string[];
  onEvent: OsEventHandler;
};

const subscribers = new Map<number, Subscriber>();
let nextId = 0;

let sharedEs: EventSource | null = null;
let sharedConnected = false;
let sharedStale = true;
let reconnectAttempts = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
const seenIdsRef = { current: new Set<string>() };
const seenIdsListRef = { current: [] as string[] };
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((fn) => fn());
}

function startConnection() {
  if (sharedEs) return;

  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  const es = new EventSource("/api/os/events");
  sharedEs = es;

  es.onopen = () => {
    reconnectAttempts = 0;
    sharedConnected = true;
    sharedStale = false;
    notify();
  };

  es.onmessage = (msg) => {
    let event: OsEvent | null;
    try {
      event = JSON.parse(msg.data as string) as OsEvent;
    } catch {
      return;
    }
    if (!event || typeof event !== "object" || !event.kind) return;

    if (event.kind === LAGGED_KIND) {
      subscribers.forEach((sub) => sub.onEvent(event));
      return;
    }

    if (event.id) {
      if (seenIdsRef.current.has(event.id)) return;
      seenIdsRef.current.add(event.id);
      seenIdsListRef.current.push(event.id);
      if (seenIdsListRef.current.length > MAX_SEEN_IDS) {
        const oldest = seenIdsListRef.current.shift();
        if (oldest) seenIdsRef.current.delete(oldest);
      }
    }

    subscribers.forEach((sub) => {
      if (sub.kinds.length > 0 && !sub.kinds.includes(event.kind)) return;
      sub.onEvent(event);
    });
  };

  es.onerror = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    sharedConnected = false;
    sharedStale = true;
    notify();

    if (es.readyState === EventSource.CLOSED) {
      sharedEs = null;
      const delay = Math.min(
        RECONNECT_DELAY_MS * 2 ** reconnectAttempts,
        MAX_RECONNECT_DELAY_MS,
      );
      reconnectAttempts += 1;
      reconnectTimer = setTimeout(() => {
        startConnection();
      }, delay);
    }
  };
}

function stopConnection() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  sharedEs?.close();
  sharedEs = null;
  sharedConnected = false;
  sharedStale = true;
  reconnectAttempts = 0;
  seenIdsRef.current.clear();
  seenIdsListRef.current = [];
  notify();
}

export function resetOsEventsState() {
  stopConnection();
  subscribers.clear();
  listeners.clear();
  nextId = 0;
}

export function useOsEvents(kinds: string[], onEvent: OsEventHandler): {
  connected: boolean;
  stale: boolean;
} {
  const [, setTick] = useState(0);
  const idRef = useRef(0);
  const onEventRef = useRef(onEvent);
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  if (idRef.current === 0) {
    idRef.current = ++nextId;
  }

  useEffect(() => {
    const id = idRef.current;
    subscribers.set(id, { kinds, onEvent: (e) => onEventRef.current(e) });
    const listener = () => setTick((t) => t + 1);
    listeners.add(listener);

    if (!sharedEs) {
      startConnection();
    }

    return () => {
      subscribers.delete(id);
      listeners.delete(listener);
      if (subscribers.size === 0 && !mountedRef.current) {
        stopConnection();
      }
    };
  }, [kinds.join(",")]);

  useEffect(() => {
    return () => {
      if (subscribers.size === 0) {
        stopConnection();
      }
    };
  }, []);

  return {
    connected: sharedConnected,
    stale: sharedStale,
  };
}
