import { describe, it, expect, beforeEach } from "vitest";
import { useTaosAgentStore } from "./taos-agent-store";

describe("taos-agent-store", () => {
  beforeEach(() => {
    useTaosAgentStore.setState({
      isOpen: false,
      messages: [],
      model: null,
      streaming: false,
    });
  });

  it("starts closed with no messages", () => {
    const state = useTaosAgentStore.getState();
    expect(state.isOpen).toBe(false);
    expect(state.messages).toHaveLength(0);
    expect(state.model).toBeNull();
    expect(state.streaming).toBe(false);
  });

  it("togglePanel opens and closes", () => {
    const { togglePanel } = useTaosAgentStore.getState();
    togglePanel();
    expect(useTaosAgentStore.getState().isOpen).toBe(true);
    togglePanel();
    expect(useTaosAgentStore.getState().isOpen).toBe(false);
  });

  it("openPanel / closePanel", () => {
    useTaosAgentStore.getState().openPanel();
    expect(useTaosAgentStore.getState().isOpen).toBe(true);
    useTaosAgentStore.getState().closePanel();
    expect(useTaosAgentStore.getState().isOpen).toBe(false);
  });

  it("appendMessage adds to messages", () => {
    const { appendMessage } = useTaosAgentStore.getState();
    appendMessage({ role: "user", content: "Hello", ts: 1 });
    expect(useTaosAgentStore.getState().messages).toHaveLength(1);
    expect(useTaosAgentStore.getState().messages[0].content).toBe("Hello");
  });

  it("appendDelta extends last assistant message", () => {
    const { appendMessage, appendDelta } = useTaosAgentStore.getState();
    appendMessage({ role: "user", content: "Hi", ts: 1 });
    appendMessage({ role: "assistant", content: "", ts: 2 });
    appendDelta("Hello");
    appendDelta(" world");
    const msgs = useTaosAgentStore.getState().messages;
    expect(msgs[1].content).toBe("Hello world");
  });

  it("setModel updates model", () => {
    useTaosAgentStore.getState().setModel("ollama/qwen3");
    expect(useTaosAgentStore.getState().model).toBe("ollama/qwen3");
  });

  it("clear removes all messages", () => {
    const { appendMessage, clear } = useTaosAgentStore.getState();
    appendMessage({ role: "user", content: "test", ts: 1 });
    clear();
    expect(useTaosAgentStore.getState().messages).toHaveLength(0);
  });

  it("setStreaming updates streaming flag", () => {
    useTaosAgentStore.getState().setStreaming(true);
    expect(useTaosAgentStore.getState().streaming).toBe(true);
    useTaosAgentStore.getState().setStreaming(false);
    expect(useTaosAgentStore.getState().streaming).toBe(false);
  });
});
