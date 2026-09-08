import { describe, it, expect, beforeEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const HTML_PATH = resolve(__dirname, "../../../../creations/taos-pocket/index.html");

function readPocketScript(): string {
  const html = readFileSync(HTML_PATH, "utf8");
  const match = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!match) throw new Error("could not locate <script> block in taos-pocket/index.html");
  return match[1];
}

function jsonResponse(body: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const mockSessions = [{ id: "s1", name: "assistant", status: "running", last_line: "Running..." }];
const mockNotifications = [{ id: "n1", time: "2m", title: "assistant", text: "Done" }];
const mockDecision = {
  id: "d1",
  question: "Continue with plan v3?",
  options: [
    { id: "o1", label: "Approve" },
    { id: "o2", label: "Revise" },
    { id: "o3", label: "Archive" },
  ],
};

function installPocket(fetchMock: ReturnType<typeof vi.fn>, opts: { mock?: boolean } = {}) {
  const script = readPocketScript()
    .replace(/const MOCK = true;/, `const MOCK = ${opts.mock === false ? "false" : "true"};`);
  const html = readFileSync(HTML_PATH, "utf8")
    .replace(/<script>[\s\S]*?<\/script>/, "");
  document.documentElement.innerHTML = html;
  (globalThis as unknown as { fetch: unknown }).fetch = fetchMock;
  new Function(script).call(globalThis);
}

async function flush(ms = 80) {
  await new Promise((r) => setTimeout(r, ms));
}

function liveFetchMock(behavior: (url: string, init?: RequestInit) => unknown) {
  return vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    return behavior(url, init);
  });
}

describe("taOS Pocket creation (creations/taos-pocket/index.html)", () => {
  beforeEach(() => {
    vi.useRealTimers();
    document.documentElement.innerHTML = "";
  });

  it("clicking the Notifications tab shows card index 1", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => jsonResponse([]));
    installPocket(fetchMock);
    await flush();

    (document.querySelector('[data-tab="1"]') as HTMLElement).click();

    const cards = document.querySelectorAll(".card");
    expect(cards.length).toBe(3);
    expect(cards[0].classList.contains("active")).toBe(false);
    expect(cards[1].classList.contains("active")).toBe(true);
    expect(cards[2].classList.contains("active")).toBe(false);
    expect(document.getElementById("topbar-title")!.textContent).toBe("Notifications");
  });

  it("clicking the Decision tab shows card index 2", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      jsonResponse({ id: "d1", question: "Q?", options: [{ id: "o1", label: "A" }] }),
    );
    installPocket(fetchMock);
    await flush();

    (document.querySelector('[data-tab="2"]') as HTMLElement).click();

    const cards = document.querySelectorAll(".card");
    expect(cards[2].classList.contains("active")).toBe(true);
    expect(document.getElementById("topbar-title")!.textContent).toBe("Decision");
  });

  it("selectOption() issues a POST with a JSON body containing option_id", async () => {
    // MOCK=false so api() routes through fetch() and we can inspect the call.
    const acceptCalls: Array<[string, RequestInit | undefined]> = [];
    const fetchMock = liveFetchMock((url, init) => {
      if (url.endsWith("/api/sessions")) return jsonResponse(mockSessions);
      if (url.endsWith("/api/notifications")) return jsonResponse(mockNotifications);
      if (url.endsWith("/api/decisions/current")) return jsonResponse(mockDecision);
      if (url.includes("/api/decisions/") && url.endsWith("/accept")) {
        acceptCalls.push([url, init]);
        return jsonResponse({ accepted: true });
      }
      return jsonResponse({ error: "not found" }, false);
    });
    installPocket(fetchMock, { mock: false });
    await flush();

    (document.querySelector('[data-tab="2"]') as HTMLElement).click();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await flush(80);

    expect(acceptCalls.length).toBe(1);
    const [, init] = acceptCalls[0];
    expect(init).toBeDefined();
    expect(init!.method).toBe("POST");
    expect(init!.body).toBe(JSON.stringify({ option_id: "o1" }));
    expect((init!.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("selectOption() leaves the decision visible when the POST rejects", async () => {
    const fetchMock = liveFetchMock((url) => {
      if (url.endsWith("/api/sessions")) return jsonResponse(mockSessions);
      if (url.endsWith("/api/notifications")) return jsonResponse(mockNotifications);
      if (url.endsWith("/api/decisions/current")) return jsonResponse(mockDecision);
      if (url.includes("/api/decisions/") && url.endsWith("/accept")) {
        throw new Error("network down");
      }
      return jsonResponse({ error: "not found" }, false);
    });
    installPocket(fetchMock, { mock: false });
    await flush();

    (document.querySelector('[data-tab="2"]') as HTMLElement).click();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await flush(80);

    const decisionCard = document.querySelectorAll(".card")[2];
    expect(decisionCard.querySelector(".decision-q")).not.toBeNull();
    expect(decisionCard.querySelectorAll(".option").length).toBe(3);
  });
});