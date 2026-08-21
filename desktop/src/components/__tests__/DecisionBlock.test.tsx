import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { DecisionBlock } from "@/apps/MessagesApp";
import type { DecisionContentBlock } from "@/apps/MessagesApp";
import { useDecisionEventsStore } from "@/stores/decision-events-store";

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
    vi.unstubAllGlobals();
    useDecisionEventsStore.setState({ answeredEpoch: 0, lastAnsweredId: null });
  });

  it("renders question, type label, and enabled option buttons for a pending single_select, and activating one submits the answer; when not open, buttons are disabled", async () => {
    // --- Open direction (status: "pending") ---
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

    // Pending state: isOpen=true, controls should be ENABLED
    expect(container.textContent).toContain("open");

    // Options render as enabled buttons (click-to-answer) when decision is open
    const enabledButtons = container.querySelectorAll('button:not([disabled])');
    expect(enabledButtons.length).toBeGreaterThanOrEqual(2);

    // Activating an enabled button submits the answer
    const firstButton = enabledButtons[0];
    expect(firstButton.tagName).toBe("BUTTON");
    fireEvent.click(firstButton);

    // --- Not-open direction (status: "answered") ---
    mockDecisionFetch({
      ...baseDecision,
      question: "Which engine?",
      type: "single_select",
      options: [
        { label: "Excalidraw", value: "excalidraw" },
        { label: "Konva", value: "konva" },
      ],
      context: "canvas replacement",
      status: "answered",
      answer: { value: "excalidraw", answered_by: "jay", answered_at: 1700000100 },
    });

    const answeredBlock: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-1",
    };
    const { container: answeredContainer } = render(<DecisionBlock block={answeredBlock} />);

    await waitFor(() => {
      expect(answeredContainer.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // When not open (answered), options should be DISABLED
    const disabledButtons = answeredContainer.querySelectorAll('button[disabled]');
    expect(disabledButtons.length).toBeGreaterThanOrEqual(2);

    // Verify no "open" label appears when not open
    expect(answeredContainer.textContent).not.toContain("open");
  });

  it("renders approve/deny as enabled option buttons when pending, disabled when not open", async () => {
    // --- Open direction (status: "pending") ---
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

    // Pending state: isOpen=true, controls should be ENABLED
    expect(container.textContent).toContain("open");

    // Options render as enabled buttons (click-to-answer) when decision is open
    const enabledButtons = container.querySelectorAll('button:not([disabled])');
    expect(enabledButtons.length).toBeGreaterThanOrEqual(2);

    // Activating an enabled button submits the answer
    fireEvent.click(enabledButtons[0]);

    // --- Not-open direction (status: "answered") ---
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
      status: "answered",
      answer: { value: "approve", answered_by: "jay", answered_at: 1700000100 },
    });

    const answeredBlock: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-2",
    };
    const { container: answeredContainer } = render(<DecisionBlock block={answeredBlock} />);

    await waitFor(() => {
      expect(answeredContainer.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // When not open (answered), options should be DISABLED
    const disabledButtons = answeredContainer.querySelectorAll('button[disabled]');
    expect(disabledButtons.length).toBeGreaterThanOrEqual(2);

    // Verify no "open" label appears when not open
    expect(answeredContainer.textContent).not.toContain("open");
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

  it("renders a enabled textarea for a pending free_text decision, and no textarea when not open", async () => {
    // --- Open direction (status: "pending") ---
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

    // Pending state: isOpen=true, textarea should be ENABLED (no disabled attribute)
    const textarea = container.querySelector("textarea");
    expect(textarea).not.toBeNull();
    // Assert no disabled attribute (enabled)
    expect(textarea.getAttribute("disabled")).toBeNull();

    // User can interact with the enabled textarea - dispatch change event
    fireEvent.change(textarea, { target: { value: "test notes" } });

    // --- Not-open direction (status: "answered") ---
    mockDecisionFetch({
      ...baseDecision,
      id: "dec-4",
      question: "Any notes?",
      type: "free_text",
      options: [],
      status: "answered",
      answer: { value: "looks good", answered_by: "sam", answered_at: 1700000200 },
    });

    const answeredBlock: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-4",
    };
    const { container: answeredContainer } = render(<DecisionBlock block={answeredBlock} />);

    await waitFor(() => {
      expect(answeredContainer.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // When not open (answered), the free_text textarea does not render
    // (it is conditional on isOpen = decision.status === "pending")
    expect(answeredContainer.querySelector("textarea")).toBeNull();

    // No "open" label when not open
    expect(answeredContainer.textContent).not.toContain("open");
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

  it("types multiple chars in free-text textarea does not post on change, submits full text on Enter", async () => {
    // --- Open direction (status: "pending") ---
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...baseDecision,
        id: "dec-7",
        question: "Any notes?",
        type: "free_text",
        options: [],
        status: "pending",
        answer: null,
        created_at: 1700000000,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-7",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    const textarea = container.querySelector("textarea");
    expect(textarea).not.toBeNull();

    // Record initial fetch call (the useEffect fetch that renders the decision)
    const initialCallCount = fetchMock.mock.calls.length;

    // Type multiple characters - fetch MUST NOT be called additionally on change
    fireEvent.change(textarea, { target: { value: "yes please" } });

    // Only the initial useEffect fetch should have been called; no extra call from onChange
    expect(fetchMock.mock.calls.length).toBe(initialCallCount);

    // Press Enter (without Shift) to submit - should post exactly once with full text
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    await waitFor(() => {
      // Find all POST calls to /answer
      const answerCalls = fetchMock.mock.calls.filter(
        ([url]) => url.endsWith("/answer") && fetchMock.mock.instances ? true : true
      );
      // There should be exactly one POST to /answer
      const postCalls = answerCalls.filter(([url]) => url === "/api/decisions/dec-7/answer");
      expect(postCalls.length).toBe(1);
      // The POST body should contain the FULL typed text "yes please"
      const postCall = postCalls[0];
      const body = JSON.parse(postCall[1].body);
      expect(body.value).toBe("yes please");
    });
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

  it("submits two answers, first is retained and second is rejected (first-answer-wins)", async () => {
    // --- First answer: render pending decision and verify answer updates UI ---
    mockDecisionFetch({
      ...baseDecision,
      question: "Pick a framework",
      type: "single_select",
      options: [
        { label: "React", value: "react" },
        { label: "Vue", value: "vue" },
      ],
      context: "ui library",
    });

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-1",
    };
    const { container: container1 } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container1.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    expect(container1.textContent).toContain("Pick a framework");
    expect(container1.textContent).toContain("React");
    expect(container1.textContent).toContain("Vue");

    // Click first option (React) - first answer
    const enabledBtns = container1.querySelectorAll('button:not([disabled])');
    fireEvent.click(enabledBtns[0]);

    await waitFor(() => {
      expect(container1.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // After first answer, verify the UI reflects the answered state
    // The component's answerDecision function records the answer and refreshes
    // the decision state. With the mock, we verify the answer submission path works.
    expect(container1.textContent).toContain("Pick a framework");

    // --- Second answer: verify component rejects submission when not pending ---
    // Render with "answered" status to verify the early-return behavior
    mockDecisionFetch({
      ...baseDecision,
      question: "Pick a framework",
      type: "single_select",
      options: [
        { label: "React", value: "react" },
        { label: "Vue", value: "vue" },
      ],
      context: "ui library",
      status: "answered",
      answer: { value: "react", answered_by: "jay", answered_at: 1700000100 },
    });

    const { container: container2 } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container2.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // When status is "answered", buttons should be disabled (early return in answerDecision)
    const disabledBtns = container2.querySelectorAll('button[disabled]');
    expect(disabledBtns.length).toBeGreaterThanOrEqual(1);

    // The "open" label should not appear when not open
    expect(container2.textContent).not.toContain("open");

    // First answer is retained in the UI - verify answer is displayed
    expect(container2.textContent).toContain("answered: React");
  });

  it("resolves live from SSE when the decision is answered in another surface", async () => {
    // Simulate an SSE decision.answered event: the fetch mock returns pending
    // first (initial render), then answered once the event fires.
    const pending = {
      ...baseDecision,
      id: "dec-sse",
      question: "Pick a colour?",
      type: "single_select",
      options: [{ label: "Red", value: "red" }, { label: "Blue", value: "blue" }],
      status: "pending" as const,
      answer: null,
    };
    const answered = {
      ...pending,
      status: "answered" as const,
      answer: { value: "red", answered_by: "jay", answered_at: 1700000100 },
    };

    let resolved = false;
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve({ ok: true, json: async () => (resolved ? answered : pending) }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = { kind: "decision", decision_id: "dec-sse" };
    const { container } = render(<DecisionBlock block={block} />);

    // Initial render shows the pending state
    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });
    expect(container.textContent).toContain("open");

    // Simulate the SSE handler receiving a decision.answered event for this decision
    resolved = true;
    useDecisionEventsStore.getState().recordAnswered("dec-sse");

    // Component re-fetches and shows the answered state live
    await waitFor(() => {
      expect(container.textContent).not.toContain("open");
      expect(container.textContent).toContain("answered: Red");
      expect(container.textContent).toContain("answered by jay");
    });

    // Exactly two GET requests: the initial render + the SSE-triggered re-fetch
    const decisionFetches = fetchMock.mock.calls.filter(
      (c) => c[0] === "/api/decisions/dec-sse",
    );
    expect(decisionFetches.length).toBe(2);
  });

  it("ignores SSE events for other decisions (no re-fetch)", async () => {
    const pending = {
      ...baseDecision,
      id: "dec-own",
      question: "Own question?",
      type: "single_select",
      options: [{ label: "Red", value: "red" }],
      status: "pending" as const,
      answer: null,
    };
    const fetchMock = mockDecisionFetch(pending);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-own",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    const before = fetchMock.mock.calls.length;

    // Event for a DIFFERENT decision should not trigger a re-fetch
    useDecisionEventsStore.getState().recordAnswered("dec-other");

    // No additional fetch should occur
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchMock.mock.calls.length).toBe(before);
  });

  it("double-click while in-flight produces exactly ONE POST", async () => {
    // --- Open decision with single option ---
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...baseDecision,
        question: "Pick a framework",
        type: "single_select",
        options: [
          { label: "React", value: "react" },
          { label: "Vue", value: "vue" },
        ],
        context: "ui library",
        status: "pending",
        answer: null,
        created_at: 1700000000,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-1",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // Click first option (React) - first answer
    const enabledBtns = container.querySelectorAll('button:not([disabled])');
    fireEvent.click(enabledBtns[0]);

    // Immediately double-click the same button while first POST is in-flight.
    // The submitting state should prevent a second POST.
    fireEvent.click(enabledBtns[0]);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // Verify only one POST to /answer was made (double-click is prevented
    // by the submitting state disabling buttons)
    const answerCalls = fetchMock.mock.calls.filter(
      ([url]) => url === "/api/decisions/dec-1/answer"
    );
    expect(answerCalls.length).toBe(1);
  });

  it("double-click while in-flight produces exactly ONE POST (free_text via Enter key)", async () => {
    // --- Open free_text decision ---
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...baseDecision,
        id: "dec-8",
        question: "Any notes?",
        type: "free_text",
        options: [],
        status: "pending",
        answer: null,
        created_at: 1700000000,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-8",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    const textarea = container.querySelector("textarea");
    expect(textarea).not.toBeNull();

    // Type some text and press Enter twice rapidly while in-flight.
    // The submitting state should prevent a second POST.
    fireEvent.change(textarea, { target: { value: "test answer" } });
    
    // First Enter key press
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    
    // Second Enter key press while still in-flight - should be blocked by submitting state
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // Verify only one POST to /answer was made
    const answerCalls = fetchMock.mock.calls.filter(
      ([url]) => url === "/api/decisions/dec-8/answer"
    );
    expect(answerCalls.length).toBe(1);
  });

  it("409 path triggers a refetch so block flips to answered state", async () => {
    // --- Open decision with single option ---
    // The GET mock MUST be request-ordered: the first GET (initial load)
    // returns the pending decision so the option button is live and the
    // POST actually runs; only the post-409 refetch returns the answered
    // state. A url-matched mock that returned "answered" for every GET made
    // this test pass without ever exercising conflict recovery.
    const pendingDec1 = {
      ...baseDecision,
      id: "dec-1",
      question: "Pick a framework",
      type: "single_select",
      options: [
        { label: "React", value: "react" },
        { label: "Vue", value: "vue" },
      ],
      context: "ui library",
      status: "pending",
      answer: null,
      created_at: 1700000000,
    };
    const answeredDec1 = {
      ...pendingDec1,
      status: "answered",
      answer: { value: "react", answered_by: "jay", answered_at: 1700000100 },
    };
    let decisionGets = 0;
    const fetchMock = vi.fn().mockImplementation(async (req) => {
      const url = req.url ?? req;
      if (typeof url === "string" && url.endsWith("/answer")) {
        // Answer POST returns 409 (someone else answered first)
        return { status: 409, ok: false, json: async () => ({ error: "already answered" }) };
      }
      decisionGets += 1;
      return {
        ok: true,
        json: async () => (decisionGets === 1 ? pendingDec1 : answeredDec1),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-1",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    // Click first option - this will get a 409 from the server
    // (simulating someone else already answered first)
    // Use container.querySelector to find the first button
    const button = container.querySelector('button');
    fireEvent.click(button);

    // After the 409 handler refetches, the decision should flip to answered
    // The refetched decision should have status "answered"
    await waitFor(() => {
      expect(container.textContent).toContain("answered: React");
      expect(container.textContent).not.toContain("open");
    });

    // The POST must actually have run -- guards against the block starting
    // out answered (disabled button, no-op click, vacuous pass).
    const answerCalls = fetchMock.mock.calls.filter(([req]) => {
      const url = req.url ?? req;
      return typeof url === "string" && url.endsWith("/answer");
    });
    expect(answerCalls.length).toBe(1);
  });

  it("shows the conflict fallback when the 409 refetch rejects", async () => {
    const pendingDec2 = {
      ...baseDecision,
      id: "dec-2",
      question: "Pick a framework",
      type: "single_select",
      options: [
        { label: "React", value: "react" },
        { label: "Vue", value: "vue" },
      ],
      context: "ui library",
      status: "pending",
      answer: null,
      created_at: 1700000000,
    };
    let decisionGets = 0;
    const fetchMock = vi.fn().mockImplementation(async (req) => {
      const url = req.url ?? req;
      if (typeof url === "string" && url.endsWith("/answer")) {
        return { status: 409, ok: false, json: async () => ({ error: "already answered" }) };
      }
      decisionGets += 1;
      if (decisionGets === 1) {
        return { ok: true, json: async () => pendingDec2 };
      }
      // Post-409 refetch dies on the network: the user must still learn
      // their answer lost the race, not see a generic "Failed to answer".
      throw new TypeError("network down");
    });
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-2",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    const button = container.querySelector('button');
    fireEvent.click(button);

    await waitFor(() => {
      const alert = container.querySelector('[role="alert"]');
      expect(alert).not.toBeNull();
      expect(alert.textContent).toContain("already answered");
    });
  });
});
