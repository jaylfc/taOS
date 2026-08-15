import { render, screen, waitFor } from "@testing-library/react";
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

  it("renders question, type label, and disabled option buttons for a pending single_select", async () => {
    mockDecisionFetch({
      ...baseDecision,
      question: "Which engine?",
      type: "single_select",
      options: [
        { label: "Excalidraw", value: "excalidraw" },
        { label: "Konva", value: "konva" },
      ],
      context: "canvas replacement",
    });

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

    // Pending state indicator
    expect(container.textContent).toContain("open");

    // Options render as disabled buttons (no click-to-answer)
    const buttons = container.querySelectorAll('button[disabled]');
    expect(buttons.length).toBeGreaterThanOrEqual(2);
  });

  it("renders approve/deny as disabled buttons for approve_deny type", async () => {
    mockDecisionFetch({
      ...baseDecision,
      id: "dec-2",
      question: "Run code_exec?",
      type: "approve_deny",
      options: [
        { label: "Approve", value: "approve" },
        { label: "Deny", value: "deny" },
      ],
      priority: "blocking",
    });

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-2",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    expect(container.textContent).toContain("Approve / Deny");
    // approve_deny uses options rendered as disabled buttons
    const buttons = container.querySelectorAll('button[disabled]');
    expect(buttons.length).toBeGreaterThanOrEqual(2);
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

  it("renders a disabled textarea for a pending free_text decision", async () => {
    mockDecisionFetch({
      ...baseDecision,
      id: "dec-4",
      question: "Any notes?",
      type: "free_text",
      options: [],
    });

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-4",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    expect(container.textContent).toContain("Free text");
    const textarea = container.querySelector("textarea[disabled]");
    expect(textarea).not.toBeNull();
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
});
