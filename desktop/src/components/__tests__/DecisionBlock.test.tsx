import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { DecisionBlock } from "@/apps/MessagesApp";
import type { DecisionContentBlock } from "@/apps/MessagesApp";

function mockDecisionFetch(decision: unknown, ok = true) {
  const fn = vi.fn().mockResolvedValue({
    ok,
    json: async () => decision,
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const baseDecision = {
  id: "dec-1",
  from_agent: "@agent",
  question: "",
  type: "single_select" as const,
  options: [] as Array<{ label: string; value: string }>,
  context: null,
  priority: "normal",
  status: "pending" as const,
  answer: null,
  created_at: 1700000000,
};

describe("DecisionBlock", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders enabled option buttons for an open pending single_select and disabled when not open", async () => {
    let getCount = 0;
    const fn = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      const isAnswer = options?.method === "POST" && url.includes("/answer");
      const isGet = !options && /\/api\/decisions\/dec-1$/.test(url);
      if (isAnswer) return { ok: true, json: async () => ({}) };
      if (isGet) {
        getCount++;
        if (getCount === 1) {
          return {
            ok: true,
            json: async () => ({
              ...baseDecision,
              id: "dec-1",
              question: "Which engine?",
              type: "single_select",
              options: [
                { label: "Excalidraw", value: "excalidraw" },
                { label: "Konva", value: "konva" },
              ],
              context: "canvas replacement",
              status: "pending",
            }),
          };
        }
        return {
          ok: true,
          json: async () => ({
            ...baseDecision,
            id: "dec-1",
            question: "Which engine?",
            type: "single_select",
            options: [
              { label: "Excalidraw", value: "excalidraw" },
              { label: "Konva", value: "konva" },
            ],
            context: "canvas replacement",
            status: "answered",
            answer: { value: "excalidraw", answered_by: "tester", answered_at: 1700000100 },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fn);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-1",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    expect(container.textContent).toContain("Which engine?");
    expect(container.textContent).toContain("canvas replacement");
    expect(container.textContent).toContain("Pick one");
    expect(container.textContent).toContain("Excalidraw");
    expect(container.textContent).toContain("Konva");

    // OPEN (status === "pending"): state indicator and enabled controls
    expect(container.textContent).toContain("open");
    const enabledButtons = container.querySelectorAll('button:not([disabled])');
    expect(enabledButtons.length).toBeGreaterThanOrEqual(2);

    // Activating an enabled button submits the answer
    const firstButton = enabledButtons[0] as HTMLElement;
    firstButton.click();
    await waitFor(() => {
      expect(fn).toHaveBeenCalledWith(
        expect.stringContaining("/api/decisions/dec-1/answer"),
        expect.anything()
      );
    });

    // NOT OPEN: after answering, options render as disabled buttons
    await waitFor(() => {
      expect(container.textContent).toContain("answered");
    });
    const disabledButtons = container.querySelectorAll('button[disabled]');
    expect(disabledButtons.length).toBeGreaterThanOrEqual(2);
  });

  it("renders enabled approve/deny buttons for an open decision and disabled when not open", async () => {
    let getCount = 0;
    const fn = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      const isAnswer = options?.method === "POST" && url.includes("/answer");
      const isGet = !options && /\/api\/decisions\/dec-2$/.test(url);
      if (isAnswer) return { ok: true, json: async () => ({}) };
      if (isGet) {
        getCount++;
        if (getCount === 1) {
          return {
            ok: true,
            json: async () => ({
              ...baseDecision,
              id: "dec-2",
              question: "Run code_exec?",
              type: "approve_deny",
              options: [
                { label: "Approve", value: "approve" },
                { label: "Deny", value: "deny" },
              ],
              priority: "blocking",
              status: "pending",
            }),
          };
        }
        return {
          ok: true,
          json: async () => ({
            ...baseDecision,
            id: "dec-2",
            question: "Run code_exec?",
            type: "approve_deny",
            options: [
              { label: "Approve", value: "approve" },
              { label: "Deny", value: "deny" },
            ],
            priority: "blocking",
            status: "answered",
            answer: { value: "approve", answered_by: "tester", answered_at: 1700000100 },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fn);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-2",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    expect(container.textContent).toContain("Approve / Deny");

    // OPEN (status === "pending"): controls are enabled
    const enabledButtons = container.querySelectorAll('button:not([disabled])');
    expect(enabledButtons.length).toBeGreaterThanOrEqual(2);

    // Activating an enabled button submits the answer
    const firstButton = enabledButtons[0] as HTMLElement;
    firstButton.click();
    await waitFor(() => {
      expect(fn).toHaveBeenCalledWith(
        expect.stringContaining("/api/decisions/dec-2/answer"),
        expect.anything()
      );
    });

    // NOT OPEN: after answering, controls are disabled
    await waitFor(() => {
      expect(container.textContent).toContain("answered");
    });
    const disabledButtons = container.querySelectorAll('button[disabled]');
    expect(disabledButtons.length).toBeGreaterThanOrEqual(2);
  });

  it("shows answer label and answerer when the decision is answered", async () => {
    mockDecisionFetch({
      ...baseDecision,
      id: "dec-3",
      question: "Pick a framework",
      type: "single_select",
      options: [{ label: "React", value: "react" }],
      status: "answered",
      answer: { value: "react", answered_by: "jay", answered_at: 1700000100 },
    });

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-3",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    expect(container.textContent).toContain("answered: React");
    expect(container.textContent).toContain("answered by jay");
  });

  it("renders an enabled textarea for an open pending free_text decision and none when not open", async () => {
    let getCount = 0;
    const fn = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      const isAnswer = options?.method === "POST" && url.includes("/answer");
      const isGet = !options && /\/api\/decisions\/dec-4$/.test(url);
      if (isAnswer) return { ok: true, json: async () => ({}) };
      if (isGet) {
        getCount++;
        if (getCount === 1) {
          return {
            ok: true,
            json: async () => ({
              ...baseDecision,
              id: "dec-4",
              question: "Any notes?",
              type: "free_text",
              options: [],
              status: "pending",
            }),
          };
        }
        return {
          ok: true,
          json: async () => ({
            ...baseDecision,
            id: "dec-4",
            question: "Any notes?",
            type: "free_text",
            options: [],
            status: "answered",
            answer: { value: "looks good", answered_by: "tester", answered_at: 1700000200 },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fn);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-4",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    expect(container.textContent).toContain("Free text");

    // OPEN (status === "pending"): textarea renders without disabled attribute
    const textarea = container.querySelector("textarea");
    expect(textarea).not.toBeNull();
    expect(textarea).not.toHaveAttribute("disabled");

    // Activating the textarea (Enter) submits the answer
    fireEvent.keyDown(textarea!, { key: "Enter", shiftKey: false });
    await waitFor(() => {
      expect(fn).toHaveBeenCalledWith(
        expect.stringContaining("/api/decisions/dec-4/answer"),
        expect.anything()
      );
    });

    // NOT OPEN: after answering, no textarea renders
    await waitFor(() => {
      expect(container.textContent).toContain("answered");
    });
    expect(container.querySelector("textarea")).toBeNull();
  });

  it("shows answered free_text value when resolved", async () => {
    mockDecisionFetch({
      ...baseDecision,
      id: "dec-5",
      question: "Any notes?",
      type: "free_text",
      options: [],
      status: "answered",
      answer: { value: "looks good", answered_by: "sam", answered_at: 1700000200 },
    });

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-5",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    expect(container.textContent).toContain("answered: looks good");
    expect(container.textContent).toContain("answered by sam");
    // No textarea while answered
    expect(container.querySelector("textarea")).toBeNull();
  });

  it("renders loading then resolved state without stale open question", async () => {
    // Decision is already answered at fetch time -- must render resolved.
    mockDecisionFetch({
      ...baseDecision,
      id: "dec-6",
      question: "Already done?",
      type: "single_select",
      options: [{ label: "Yes", value: "yes" }, { label: "No", value: "no" }],
      status: "answered",
      answer: { value: "no", answered_by: "dev", answered_at: 1700000300 },
    });

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-6",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    expect(container.textContent).toContain("answered by dev");
    expect(container.textContent).toContain("answered: No");
    // The stale "open" label must NOT appear once answered.
    expect(container.textContent).not.toContain("open");
  });

  it("shows error state when the decision fetch fails", async () => {
    mockDecisionFetch(null, false);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-missing",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.textContent).toContain("decision not found");
    });
  });

  it("rejects subsequent answers after the first is accepted", async () => {
    let answerCount = 0;
    let getCount = 0;
    const fn = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      const isAnswer = options?.method === "POST" && url.includes("/answer");
      const isGet = !options && /\/api\/decisions\/dec-faw$/.test(url);
      if (isAnswer) {
        answerCount++;
        if (answerCount === 1) {
          return { ok: true, json: async () => ({}) };
        }
        return { ok: false, status: 409, json: async () => ({ detail: "Decision already answered" }) };
      }
      if (isGet) {
        getCount++;
        if (getCount === 1) {
          return {
            ok: true,
            json: async () => ({
              ...baseDecision,
              id: "dec-faw",
              question: "Pick one",
              type: "single_select",
              options: [
                { label: "Excalidraw", value: "excalidraw" },
                { label: "Konva", value: "konva" },
              ],
              status: "pending",
            }),
          };
        }
        return {
          ok: true,
          json: async () => ({
            ...baseDecision,
            id: "dec-faw",
            question: "Pick one",
            type: "single_select",
            options: [
              { label: "Excalidraw", value: "excalidraw" },
              { label: "Konva", value: "konva" },
            ],
            status: "answered",
            answer: { value: "excalidraw", answered_by: "tester", answered_at: 1700000100 },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fn);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-faw",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // Submit first answer
    const firstButton = container.querySelector('button:not([disabled])') as HTMLElement;
    firstButton.click();

    // Wait for the decision to be answered
    await waitFor(() => expect(container.textContent).toContain("answered"));

    // Attempt a second answer by firing click on a button
    const anyButton = container.querySelector('button') as HTMLElement;
    fireEvent.click(anyButton);

    // First answer retained, second rejected
    expect(container.textContent).toContain("answered: Excalidraw");
    expect(answerCount).toBe(1);
  });
});
