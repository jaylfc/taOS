export const RECONNECT_DELAY_MS = 5000;
export const MAX_RECONNECT_DELAY_MS = 30000;
export const MAX_SEEN_IDS = 128;

export type SseStatus = { connected: boolean; stale: boolean };

export class ReconnectManager {
  private attempts = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly onReconnect: () => void) {}

  get hasTimer(): boolean {
    return this.timer !== null;
  }

  schedule(): number {
    if (this.timer) return this.attempts;
    const delay = Math.min(
      RECONNECT_DELAY_MS * 2 ** this.attempts,
      MAX_RECONNECT_DELAY_MS,
    );
    this.attempts += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.onReconnect();
    }, delay);
    return delay;
  }

  reset() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.attempts = 0;
  }

  cancel() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

export function createSseConnection(opts: {
  url: string;
  onMessage: (msg: MessageEvent) => void;
  onOpen?: () => void;
  onError?: () => void;
  getMessageId?: (msg: MessageEvent) => string | undefined;
}): () => void {
  const manager = new ReconnectManager(() => connect());
  let es: EventSource | null = null;
  let stopped = false;
  const seenIds: string[] = [];
  const seen = new Set<string>();

  const alreadySeen = (id: string | undefined): boolean => {
    if (!id) return false;
    if (seen.has(id)) return true;
    seen.add(id);
    seenIds.push(id);
    if (seenIds.length > MAX_SEEN_IDS) {
      const oldest = seenIds.shift();
      if (oldest) seen.delete(oldest);
    }
    return false;
  };

  const connect = () => {
    if (stopped) return;
    es = new EventSource(opts.url);

    es.onopen = () => {
      manager.reset();
      opts.onOpen?.();
    };

    es.onmessage = (msg: MessageEvent) => {
      if (opts.getMessageId && alreadySeen(opts.getMessageId(msg))) return;
      opts.onMessage(msg);
    };

    es.onerror = () => {
      opts.onError?.();
      if (stopped) return;
      if (es?.readyState === EventSource.CLOSED) {
        manager.schedule();
      }
    };
  };

  connect();

  return () => {
    stopped = true;
    manager.cancel();
    es?.close();
  };
}
