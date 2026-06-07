export interface AttachmentMeta {
  mime_type: string;
  url: string;
  filename?: string;
}

export interface AssistantMessage {
  role: "user" | "assistant" | "system";
  content: string;
  attachments?: AttachmentMeta[];
  ts: number;
}

import { create } from "zustand";

interface TaosAgentState {
  isOpen: boolean;
  messages: AssistantMessage[];
  model: string | null;
  streaming: boolean;
  settingsOpen: boolean;
}

interface TaosAgentActions {
  togglePanel: () => void;
  openPanel: () => void;
  closePanel: () => void;
  setMessages: (messages: AssistantMessage[]) => void;
  appendMessage: (msg: AssistantMessage) => void;
  appendDelta: (delta: string) => void;
  setModel: (model: string | null) => void;
  setStreaming: (streaming: boolean) => void;
  setSettingsOpen: (open: boolean) => void;
  clear: () => void;
}

export type TaosAgentStore = TaosAgentState & TaosAgentActions;

export const useTaosAgentStore = create<TaosAgentStore>((set) => ({
  isOpen: false,
  messages: [],
  model: null,
  streaming: false,
  settingsOpen: false,

  togglePanel: () => set((s) => ({ isOpen: !s.isOpen })),
  openPanel: () => set({ isOpen: true }),
  closePanel: () => set({ isOpen: false }),

  setMessages: (messages) => set({ messages }),

  appendMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg] })),

  appendDelta: (delta) =>
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") {
        msgs[msgs.length - 1] = { ...last, content: last.content + delta };
      }
      return { messages: msgs };
    }),

  setModel: (model) => set({ model }),
  setStreaming: (streaming) => set({ streaming }),
  setSettingsOpen: (open) => set({ settingsOpen: open }),
  clear: () => set({ messages: [] }),
}));
