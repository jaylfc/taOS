import { render, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { DecisionBlock } from "@/apps/MessagesApp";
import type { DecisionContentBlock } from "@/apps/MessagesApp";

const baseFreeTextDecision = {
  id: "dec-1",
  from_agent: "@agent",
  question: "Any notes?",
  type: "free_text" as const,
  options: [] as Array<{ label: string; value: string }>,
  context: null,
  priority: "normal",
  status: "pending" as const,
  answer: null,
  created_at: 1700000000,
};

function pendingFreeText(id: string) {
  return { ...baseFreeTextDecision, id, question: "Any notes?" };
}

function answeredFreeText(id: string, answerValue = "some answer") {
  return {
    ...baseFreeTextDecision,
    id,
    status: "answered" as const,
    answer: { value: answerValue, answered_by: "someone", answered_at: 1700000001 },
  };
}

describe("RED PROOF: controlled textarea trim-on-change", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("retains a trailing space while the user is mid-word (typing 'hello world')", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => pendingFreeText("dec-red"),
      }),
    );

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-red",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea).not.toBeNull();

    fireEvent.change(textarea, { target: { value: "hello " } });

    expect(textarea.value).toBe("hello ");
  });

  it("Enter submits POST body.value === 'hello world' after typing the full phrase", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      if (options?.method === "POST") {
        return { ok: true, status: 200, json: async () => ({ success: true }) };
      }
      return { ok: true, status: 200, json: async () => pendingFreeText("dec-submit") };
    });
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-submit",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "hello world" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([url, opts]) => opts?.method === "POST" && url === "/api/decisions/dec-submit/answer",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(postCall![1].body as string);
      expect(body.value).toBe("hello world");
    });
  });

  it("Enter submits trimmed value when raw input has surrounding whitespace ('  x  ' -> 'x')", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      if (options?.method === "POST") {
        return { ok: true, status: 200, json: async () => ({ success: true }) };
      }
      return { ok: true, status: 200, json: async () => pendingFreeText("dec-trim") };
    });
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-trim",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "  x  " } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([url, opts]) => opts?.method === "POST" && url === "/api/decisions/dec-trim/answer",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(postCall![1].body as string);
      expect(body.value).toBe("x");
    });
  });
});

describe("RED PROOF: 409 refetch + non-409 error surfacing", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("409 refetches and flips the block to the answered state (no alert)", async () => {
    let getCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      if (options?.method === "POST") {
        return {
          ok: false,
          status: 409,
          json: async () => ({ error: "already answered or not pending" }),
        };
      }
      getCalls++;
      if (getCalls === 1) {
        return { ok: true, status: 200, json: async () => pendingFreeText("dec-409") };
      }
      return { ok: true, status: 200, json: async () => answeredFreeText("dec-409") };
    });
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-409",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "some answer" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

    await waitFor(() => {
      expect(container.textContent).toContain("answered:");
    });
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("surfaces the exact non-409 server error message in role=alert", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, options?: RequestInit) => {
      if (options?.method === "POST") {
        return {
          ok: false,
          status: 500,
          json: async () => ({ error: "boom" }),
        };
      }
      return { ok: true, status: 200, json: async () => pendingFreeText("dec-500") };
    });
    vi.stubGlobal("fetch", fetchMock);

    const block: DecisionContentBlock = {
      kind: "decision",
      decision_id: "dec-500",
    };
    const { container } = render(<DecisionBlock block={block} />);

    await waitFor(() => {
      expect(container.querySelector('[data-decision-block="true"]')).not.toBeNull();
    });

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "some answer" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

    await waitFor(() => {
      const alert = container.querySelector('[role="alert"]');
      expect(alert).not.toBeNull();
      expect(alert!.textContent).toContain("boom");
    });
  });
});
