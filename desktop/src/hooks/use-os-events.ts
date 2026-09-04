import { useEffect, useRef, useSyncExternalStore } from "react";

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

export type OsEventsStatus = {
  connected: boolean;
  stale: boolean;
};

const subscribers = new Map<number, Subscriber>();
let nextId = 0;

let sharedEs: EventSource | null = null;
let reconnectAttempts = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
const seenIdsRef = { current: new Set<string>() };
const seenIdsListRef = { current: [] as string[] };
const listeners = new Set<() => void>();

const DISCONNECTED: OsEventsStatus = { connected: false, stale: true };

// The snapshot every subscriber renders from. It is replaced, never mutated,
// so useSyncExternalStore can compare it by identity.
let status: OsEventsStatus = DISCONNECTED;

function getStatus(): OsEventsStatus {
  return status;
}

function subscribeToStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// A transition that lands on the status we already publish must not notify.
// The browser fires `error` again on every failed retry while it reconnects,
// and re-publishing an identical snapshot would re-render every subscriber for
// a change none of their UIs can tell apart.
function setStatus(connected: boolean, stale: boolean) {
  if (status.connected === connected && status.stale === stale) return;
  status = { connected, stale };
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
    setStatus(true, false);
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
    // An `error` means the stream is down RIGHT NOW, whether or not the browser
    // means to retry it. Nothing resumes the gap -- the endpoint sends no SSE
    // `id:` line and ignores `Last-Event-ID` -- so a subscriber told it is
    // still live would silently miss every change until the retry lands.
    // Report it; the no-op guard in setStatus is what keeps a long retry storm
    // from thrashing anyone's UI.
    setStatus(false, true);

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
  reconnectAttempts = 0;
  seenIdsRef.current.clear();
  seenIdsListRef.current = [];
  setStatus(false, true);
}

export function resetOsEventsState() {
  stopConnection();
  subscribers.clear();
  listeners.clear();
  nextId = 0;
  status = DISCONNECTED;
}

export function useOsEvents(
  kinds: string[],
  onEvent: OsEventHandler,
): OsEventsStatus {
  const onEventRef = useRef(onEvent);
  const kindsRef = useRef(kinds);
  const idRef = useRef<number | null>(null);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  // Registration happens in an effect, never in the render body. Allocating the
  // id while rendering is a side effect inside a function React may call
  // speculatively and throw away, which burns an id no subscriber owns.
  //
  // The dependency list is empty on purpose: the subscriber's LIFETIME is the
  // component's, and only its `kinds` change. That leaves exactly one rule for
  // the stream -- open while the map is non-empty -- read from the map itself
  // rather than from the unmounting component's own mounted flag, which cannot
  // see that a different component is mid-update in the same commit.
  useEffect(() => {
    const id = ++nextId;
    idRef.current = id;
    subscribers.set(id, {
      kinds: kindsRef.current,
      onEvent: (e) => onEventRef.current(e),
    });
    startConnection();

    return () => {
      idRef.current = null;
      subscribers.delete(id);
      if (subscribers.size === 0) {
        stopConnection();
      }
    };
  }, []);

  // `kindsKey` is the value identity of `kinds`: a fresh array holding the same
  // entries must not churn the registration.
  const kindsKey = kinds.join(",");
  useEffect(() => {
    kindsRef.current = kinds;
    const id = idRef.current;
    if (id === null) return;
    const sub = subscribers.get(id);
    if (sub) sub.kinds = kinds;
  }, [kindsKey]);

  return useSyncExternalStore(subscribeToStatus, getStatus, getStatus);
}
