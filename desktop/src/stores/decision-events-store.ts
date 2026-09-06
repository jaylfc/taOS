import { create } from "zustand";

/**
 * Lightweight broadcast channel for decision lifecycle events that arrive over
 * SSE (via useEventStream's `decision.answered` handler). Components subscribe
 * to re-fetch their own slice of decision state instead of polling.
 *
 * `answeredEpoch` bumps on every answered event so ANY component that cares
 * about decisions can trigger a silent refresh (e.g. the Decisions app list).
 * `lastAnsweredId` lets a component that owns ONE decision (the chat
 * DecisionBlock) skip the network call entirely when the event is unrelated.
 */
interface DecisionEventsState {
  answeredEpoch: number;
  lastAnsweredId: string | null;
  recordAnswered: (decisionId: string) => void;
}

export const useDecisionEventsStore = create<DecisionEventsState>((set, get) => ({
  answeredEpoch: 0,
  lastAnsweredId: null,
  recordAnswered: (decisionId: string) =>
    set({
      answeredEpoch: get().answeredEpoch + 1,
      lastAnsweredId: decisionId,
    }),
}));
