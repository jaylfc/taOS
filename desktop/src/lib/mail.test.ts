import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchAccounts,
  addAccount,
  deleteAccount,
  fetchFolders,
  fetchMessages,
  fetchMessage,
  sendMessage,
} from "./mail";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("fetchAccounts", () => {
  it("calls GET /api/mail/accounts and returns parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [
        {
          id: "acc-1",
          display_name: "Test",
          email_address: "test@example.com",
          imap_host: "imap.example.com",
          imap_port: 993,
          imap_security: "tls",
          smtp_host: "smtp.example.com",
          smtp_port: 587,
          smtp_security: "starttls",
          username: "test@example.com",
          created_at: 1000,
          updated_at: 2000,
        },
      ],
    });
    global.fetch = fetchMock;

    const result = await fetchAccounts();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/mail/accounts");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("acc-1");
    expect(result[0].email_address).toBe("test@example.com");
  });

  it("throws on non-ok response with error body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: "internal error" }),
    });

    await expect(fetchAccounts()).rejects.toThrow("internal error");
  });

  it("throws with HTTP status when body has no error", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({}),
    });

    await expect(fetchAccounts()).rejects.toThrow("HTTP 503");
  });
});

describe("addAccount", () => {
  it("posts JSON body and returns parsed account", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "acc-new",
        display_name: "New",
        email_address: "new@example.com",
        imap_host: "imap.example.com",
        imap_port: 993,
        imap_security: "tls",
        smtp_host: "smtp.example.com",
        smtp_port: 587,
        smtp_security: "starttls",
        username: "new@example.com",
        created_at: 1000,
        updated_at: 1000,
      }),
    });
    global.fetch = fetchMock;

    const newAccount = {
      display_name: "New",
      email_address: "new@example.com",
      imap_host: "imap.example.com",
      imap_port: 993,
      imap_security: "tls",
      smtp_host: "smtp.example.com",
      smtp_port: 587,
      smtp_security: "starttls",
      username: "new@example.com",
      password: "secret",
    };

    const result = await addAccount(newAccount);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/mail/accounts");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.email_address).toBe("new@example.com");
    expect(body.password).toBe("secret");
    expect(result.id).toBe("acc-new");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "validation failed" }),
    });

    await expect(
      addAccount({
        display_name: "X",
        email_address: "x@example.com",
        imap_host: "imap.example.com",
        imap_port: 993,
        imap_security: "tls",
        smtp_host: "smtp.example.com",
        smtp_port: 587,
        smtp_security: "starttls",
        username: "x@example.com",
        password: "pw",
      }),
    ).rejects.toThrow("validation failed");
  });
});

describe("deleteAccount", () => {
  it("sends DELETE to the account URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    });
    global.fetch = fetchMock;

    await deleteAccount("acc-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/mail/accounts/acc-1");
    expect(opts.method).toBe("DELETE");
  });

  it("encodes accountId in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    });
    global.fetch = fetchMock;

    await deleteAccount("acc/with/slashes");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/mail/accounts/acc%2Fwith%2Fslashes");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: "not found" }),
    });

    await expect(deleteAccount("acc-1")).rejects.toThrow("not found");
  });
});

describe("fetchFolders", () => {
  it("calls GET folders URL and returns folder list", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ folders: ["INBOX", "Sent", "Drafts"] }),
    });
    global.fetch = fetchMock;

    const result = await fetchFolders("acc-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/mail/accounts/acc-1/folders");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result).toEqual(["INBOX", "Sent", "Drafts"]);
  });

  it("returns [] when folders is not an array", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ folders: null }),
    });

    const result = await fetchFolders("acc-1");
    expect(result).toEqual([]);
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: "server error" }),
    });

    await expect(fetchFolders("acc-1")).rejects.toThrow("server error");
  });
});

describe("fetchMessages", () => {
  it("calls GET messages URL with folder and limit params", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        messages: [
          {
            uid: "100",
            from_name: "Sender",
            from_addr: "sender@example.com",
            to: "me@example.com",
            subject: "Hello",
            date: "2025-01-01",
            snippet: "Hi there",
            unread: true,
            flagged: false,
            has_attachment: false,
          },
        ],
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchMessages("acc-1", "INBOX", 25);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/mail/accounts/acc-1/messages?");
    expect(url).toContain("folder=INBOX");
    expect(url).toContain("limit=25");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result).toHaveLength(1);
    expect(result[0].uid).toBe("100");
    expect(result[0].subject).toBe("Hello");
  });

  it("uses default limit of 50", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ messages: [] }),
    });
    global.fetch = fetchMock;

    await fetchMessages("acc-1", "INBOX");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("limit=50");
  });

  it("returns [] when messages is not an array", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ messages: null }),
    });

    const result = await fetchMessages("acc-1", "INBOX");
    expect(result).toEqual([]);
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ error: "forbidden" }),
    });

    await expect(fetchMessages("acc-1", "INBOX")).rejects.toThrow("forbidden");
  });
});

describe("fetchMessage", () => {
  it("calls GET message URL with folder param and returns detail", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        uid: "100",
        from_name: "Sender",
        from_addr: "sender@example.com",
        to: "me@example.com",
        cc: "",
        subject: "Hello",
        date: "2025-01-01",
        body_text: "Hi there",
        body_html: "<p>Hi there</p>",
        attachments: [],
      }),
    });
    global.fetch = fetchMock;

    const result = await fetchMessage("acc-1", "100", "INBOX");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/mail/accounts/acc-1/messages/100?");
    expect(url).toContain("folder=INBOX");
    expect((opts.headers?.get?.("Accept") ?? opts.headers?.Accept)).toBe("application/json");
    expect(result.uid).toBe("100");
    expect(result.body_text).toBe("Hi there");
    expect(result.attachments).toEqual([]);
  });

  it("encodes uid in the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        uid: "a/b",
        from_name: "",
        from_addr: "",
        to: "",
        cc: "",
        subject: "",
        date: "",
        body_text: "",
        body_html: "",
        attachments: [],
      }),
    });
    global.fetch = fetchMock;

    await fetchMessage("acc-1", "a/b", "INBOX");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/messages/a%2Fb?");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ error: "message not found" }),
    });

    await expect(fetchMessage("acc-1", "999", "INBOX")).rejects.toThrow(
      "message not found",
    );
  });
});

describe("sendMessage", () => {
  it("posts JSON body to send URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    });
    global.fetch = fetchMock;

    const payload = {
      to: "recipient@example.com",
      subject: "Test",
      body: "Hello world",
    };

    await sendMessage("acc-1", payload);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/mail/accounts/acc-1/send");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    const body = JSON.parse(opts.body);
    expect(body.to).toBe("recipient@example.com");
    expect(body.subject).toBe("Test");
    expect(body.body).toBe("Hello world");
  });

  it("includes cc when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
    });
    global.fetch = fetchMock;

    await sendMessage("acc-1", {
      to: "a@b.com",
      subject: "S",
      body: "B",
      cc: "c@d.com",
    });

    const [, opts] = fetchMock.mock.calls[0];
    const body = JSON.parse(opts.body);
    expect(body.cc).toBe("c@d.com");
  });

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: "send failed" }),
    });

    await expect(
      sendMessage("acc-1", { to: "a@b.com", subject: "S", body: "B" }),
    ).rejects.toThrow("send failed");
  });
});
