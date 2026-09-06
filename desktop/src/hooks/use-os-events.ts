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
  // Per subscriber, NOT shared. One stream carries the union of every
  // subscribed kind, so a shared budget would let a busy kind evict a quiet
  // subscriber's ids, and the server replays the tail of each channel
  // independently on reconnect -- the quiet subscriber would then see an event
  // it already handled and refetch for nothing. Per subscriber, this is the
  // budget each caller had back when it owned a stream of its own.
  seenIds: Set<string>;
  seenIdsOrder: string[];
};

export type OsEventsStatus = {
  connected: boolean;
  stale: boolean;
};

// `null` means "no filter, every kind". An empty array means nothing has been
// committed yet, which only happens while there are no subscribers.
type Coverage = string[] | null;

const subscribers = new Map<number, Subscriber>();
let nextId = 0;

let sharedEs: EventSource | null = null;
// A widened stream that has not opened yet. The narrow one keeps delivering
// until it does.
let pendingEs: EventSource | null = null;
// What we WANT served: the widest set asked for since the current run of
// subscribers began. It only grows, so it converges -- at most one reopen per
// distinct kind for as long as at least one subscriber stays mounted. It resets
// with the connection when the last one leaves.
let targetKinds: Coverage = [];
// What is actually ON THE WIRE. Kept apart from the target because a widening
// that fails must not look served; if it did, nothing would ever retry it and
// the kind it added would stay filtered out with its subscriber getting silence.
let servedKinds: Coverage = [];
// What the in-flight widened stream is aiming at.
let pendingKinds: Coverage = [];
let reconnectAttempts = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();
let stopScheduled = false;

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

function unionKinds(a: Coverage, b: Coverage): Coverage {
  // "Every kind" absorbs anything narrower.
  if (a === null || b === null) return null;
  const out = new Set(a);
  b.forEach((kind) => out.add(kind));
  return [...out].sort();
}

function sameKinds(a: Coverage, b: Coverage): boolean {
  if (a === null || b === null) return a === b;
  return a.length === b.length && a.every((kind, i) => kind === b[i]);
}

// The union of what every live subscriber asked for. A subscriber with an empty
// list means "all kinds", which collapses the union to no filter.
function desiredKinds(): Coverage {
  let wanted: Coverage = [];
  for (const sub of subscribers.values()) {
    if (sub.kinds.length === 0) return null;
    wanted = unionKinds(wanted, sub.kinds);
  }
  return wanted;
}

function streamUrl(kinds: Coverage): string {
  if (kinds === null || kinds.length === 0) return "/api/os/events";
  return `/api/os/events?kinds=${kinds.map(encodeURIComponent).join(",")}`;
}

// Every subscriber shares one dispatch loop now, so a handler that throws would
// abort `forEach` and silently rob every subscriber after it of the event --
// `events.lagged` included, the one frame that can never be recovered by a
// refetch the caller never learns it needs. Back when each caller owned its own
// EventSource a throw could only break the caller that threw; the isolation
// boundary is what keeps that property across the multiplex. Reported the way
// the rest of the desktop reports a failed user callback: warn and carry on.
function dispatch(sub: Subscriber, event: OsEvent) {
  try {
    sub.onEvent(event);
  } catch (err) {
    console.warn("os events: subscriber handler failed", event.kind, err);
  }
}

function handleMessage(msg: MessageEvent) {
  let event: OsEvent | null;
  try {
    event = JSON.parse(msg.data as string) as OsEvent;
  } catch {
    return;
  }
  if (!event || typeof event !== "object" || !event.kind) return;

  if (event.kind === LAGGED_KIND) {
    subscribers.forEach((sub) => dispatch(sub, event));
    return;
  }

  const id = event.id;
  subscribers.forEach((sub) => {
    if (sub.kinds.length > 0 && !sub.kinds.includes(event.kind)) return;
    if (id) {
      if (sub.seenIds.has(id)) return;
      sub.seenIds.add(id);
      sub.seenIdsOrder.push(id);
      if (sub.seenIdsOrder.length > MAX_SEEN_IDS) {
        const oldest = sub.seenIdsOrder.shift();
        if (oldest) sub.seenIds.delete(oldest);
      }
    }
    dispatch(sub, event);
  });
}

function openStream(kinds: Coverage): EventSource {
  const es = new EventSource(streamUrl(kinds));
  es.onmessage = handleMessage;

  es.onopen = () => {
    reconnectAttempts = 0;
    if (es === pendingEs) {
      // The widened stream is live, so the narrow one can go now -- not before.
      // Overlapping the two is what keeps delivery unbroken across a filter
      // change, and the per-subscriber dedup drops whatever arrives on both.
      sharedEs?.close();
      sharedEs = pendingEs;
      servedKinds = pendingKinds;
      pendingEs = null;
      pendingKinds = [];
    }
    setStatus(true, false);
  };

  es.onerror = () => {
    if (es === pendingEs) {
      // The widened stream failed. If the narrow one is still up it keeps
      // delivering everything it covers, so drop the attempt and let the next
      // mount or kinds change retry the widening. If it is NOT up, deferring
      // to this stream is what left the backoff unscheduled, so schedule it.
      if (es.readyState === EventSource.CLOSED) {
        es.close();
        pendingEs = null;
        pendingKinds = [];
        if (!sharedEs) scheduleReconnect();
      }
      return;
    }
    // A stream we already handed off from has nothing left to say.
    if (es !== sharedEs) return;

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
      // A widened stream is already on its way and covers everything this one
      // did, so it IS the reconnect. Scheduling another on top would open a
      // stream the handoff then immediately closes.
      if (!pendingEs) scheduleReconnect();
    }
  };

  return es;
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  const delay = Math.min(
    RECONNECT_DELAY_MS * 2 ** reconnectAttempts,
    MAX_RECONNECT_DELAY_MS,
  );
  reconnectAttempts += 1;
  reconnectTimer = setTimeout(() => {
    // Clear the handle FIRST: startConnection reads it, and a fired timer
    // whose handle is still set would refuse its own reconnect.
    reconnectTimer = null;
    startConnection();
  }, delay);
}

// Widen the server-side filter to cover everything subscribed, without a gap.
//
// The filter has to be applied upstream: the relay in
// `tinyagentos/routes/os_events.py` drops unrequested kinds BEFORE its bounded
// 256-slot queue, so a kind nobody asked for can never occupy a slot, evict an
// event somebody did ask for, and raise an `events.lagged` that from the
// subscriber's side never happened. Filtering only in the browser gives that
// property up.
//
// Coverage never narrows. Widening is the only thing that costs a reopen, so
// shrinking it when a subscriber leaves would just buy another reopen when the
// next one arrives. Monotone, it converges instead.
function ensureCoverage() {
  targetKinds = unionKinds(targetKinds, desiredKinds());

  // Nothing is open yet, so whatever opens next picks the target up.
  if (!sharedEs) return;
  // The wire already carries it.
  if (sameKinds(servedKinds, targetKinds)) return;
  // A stream aiming at it is already on its way.
  if (pendingEs && sameKinds(pendingKinds, targetKinds)) return;

  pendingEs?.close();
  pendingKinds = targetKinds;
  pendingEs = openStream(targetKinds);
}

function startConnection() {
  if (sharedEs) return;

  // A widened stream in flight already covers the target, so it IS the
  // connection in progress -- opening another beside it just gets closed by
  // the handoff a moment later.
  if (pendingEs) return;

  // Every subscriber that mounts calls this. If a retry is already scheduled,
  // let it run: cancelling it and connecting immediately would make a view
  // that mounts callers in a loop retry at mount frequency against a down
  // endpoint instead of at the intended 5s -> 30s spacing.
  if (reconnectTimer) return;

  servedKinds = targetKinds;
  sharedEs = openStream(targetKinds);
}

function stopConnection() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  pendingEs?.close();
  pendingEs = null;
  sharedEs?.close();
  sharedEs = null;
  targetKinds = [];
  servedKinds = [];
  pendingKinds = [];
  reconnectAttempts = 0;
  setStatus(false, true);
}

// The map goes empty for a moment in any commit that swaps one subscriber for
// another, because React runs every cleanup in a commit before any setup.
// Deferring the decision to a microtask -- which cannot run until that whole
// flush has unwound -- means "the map is empty" is read once the commit has
// settled, not mid-teardown.
function stopConnectionIfIdle() {
  if (stopScheduled) return;
  stopScheduled = true;
  queueMicrotask(() => {
    // A reset cancels a pending check by clearing the flag.
    if (!stopScheduled) return;
    stopScheduled = false;
    if (subscribers.size === 0) {
      stopConnection();
    }
  });
}

export function resetOsEventsState() {
  stopScheduled = false;
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
  // the stream -- open while the map is non-empty, read from the map itself
  // once the commit has settled -- instead of the unmounting component's own
  // mounted flag, which cannot see that another component is mid-update in the
  // same commit.
  useEffect(() => {
    const id = ++nextId;
    idRef.current = id;
    subscribers.set(id, {
      kinds: kindsRef.current,
      onEvent: (e) => onEventRef.current(e),
      seenIds: new Set<string>(),
      seenIdsOrder: [],
    });
    ensureCoverage();
    startConnection();

    return () => {
      idRef.current = null;
      subscribers.delete(id);
      stopConnectionIfIdle();
    };
  }, []);

  // `kindsKey` is the value identity of `kinds`: a fresh array holding the same
  // entries must not churn the registration or the coverage.
  const kindsKey = kinds.join(",");
  useEffect(() => {
    kindsRef.current = kinds;
    const id = idRef.current;
    if (id === null) return;
    const sub = subscribers.get(id);
    if (sub) sub.kinds = kinds;
    ensureCoverage();
  }, [kindsKey]);

  return useSyncExternalStore(subscribeToStatus, getStatus, getStatus);
}
