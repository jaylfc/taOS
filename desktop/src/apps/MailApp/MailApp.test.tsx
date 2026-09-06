import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { MailApp } from "./index";
import * as mailApi from "@/lib/mail";
import * as useIsMobileModule from "@/hooks/use-is-mobile";

vi.mock("@/lib/mail");
vi.mock("@/hooks/use-is-mobile");

const account: mailApi.MailAccount = {
  id: "acct-1",
  display_name: "Jay Lawrence",
  email_address: "jay@taos.my",
  imap_host: "imap.taos.my",
  imap_port: 993,
  imap_security: "ssl",
  smtp_host: "smtp.taos.my",
  smtp_port: 587,
  smtp_security: "starttls",
  username: "jay@taos.my",
  created_at: 0,
  updated_at: 0,
};

const envelopes: mailApi.MailEnvelope[] = [
  {
    uid: "1",
    from_name: "Dhaval Patel",
    from_addr: "dhaval@example.com",
    to: "jay@taos.my",
    subject: "AssetOpsBench integration",
    date: "Mon, 15 Jun 2026 09:24:00 +0000",
    snippet: "Thanks for the quick turnaround on the connector.",
    unread: true,
    flagged: false,
    has_attachment: true,
  },
  {
    uid: "2",
    from_name: "Coolify",
    from_addr: "noreply@coolify.io",
    to: "jay@taos.my",
    subject: "Deployment succeeded",
    date: "Mon, 14 Jun 2026 09:24:00 +0000",
    snippet: "Build finished in 42s.",
    unread: false,
    flagged: true,
    has_attachment: false,
  },
];

describe("MailApp", () => {
  beforeEach(() => {
    vi.mocked(useIsMobileModule.useIsMobile).mockReturnValue(false);
    vi.mocked(mailApi.fetchAccounts).mockResolvedValue([account]);
    vi.mocked(mailApi.fetchFolders).mockResolvedValue(["INBOX", "Sent"]);
    vi.mocked(mailApi.fetchMessages).mockResolvedValue(envelopes);
    vi.mocked(mailApi.fetchMessage).mockResolvedValue({
      uid: "1",
      from_name: "Dhaval Patel",
      from_addr: "dhaval@example.com",
      to: "jay@taos.my",
      cc: "",
      subject: "AssetOpsBench integration",
      date: "Mon, 15 Jun 2026 09:24:00 +0000",
      body_text: "Benchmark harness runs clean.",
      body_html: "",
      attachments: [{ filename: "notes.pdf", content_type: "application/pdf", size: 248000 }],
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the message list from the backend", async () => {
    render(<MailApp windowId="w1" />);
    expect(await screen.findByText("AssetOpsBench integration")).toBeInTheDocument();
    expect(screen.getByText("Coolify")).toBeInTheDocument();
  });

  it("filters to unread when the Unread tab is selected", async () => {
    render(<MailApp windowId="w1" />);
    await screen.findByText("AssetOpsBench integration");
    fireEvent.click(screen.getByRole("tab", { name: /Unread/ }));
    expect(screen.getByText("AssetOpsBench integration")).toBeInTheDocument();
    expect(screen.queryByText("Deployment succeeded")).not.toBeInTheDocument();
  });

  it("opens a message into the reading pane", async () => {
    render(<MailApp windowId="w1" />);
    fireEvent.click(await screen.findByText("AssetOpsBench integration"));
    expect(await screen.findByText("Benchmark harness runs clean.")).toBeInTheDocument();
    expect(screen.getByText("notes.pdf")).toBeInTheDocument();
  });

  it("shows the add-account form when there are no accounts", async () => {
    vi.mocked(mailApi.fetchAccounts).mockResolvedValue([]);
    render(<MailApp windowId="w1" />);
    expect(await screen.findByText("Add a mail account")).toBeInTheDocument();
  });

  it("exposes a share / send-to entry point in the reading toolbar", async () => {
    render(<MailApp windowId="w1" />);
    fireEvent.click(await screen.findByText("AssetOpsBench integration"));
    await screen.findByText("Benchmark harness runs clean.");
    const shareBtn = screen.getByTitle("Share / Send to");
    fireEvent.click(shareBtn);
    expect(await screen.findByText("Send to a person or agent")).toBeInTheDocument();
  });

  it("sends a composed message via the backend", async () => {
    vi.mocked(mailApi.sendMessage).mockResolvedValue();
    render(<MailApp windowId="w1" />);
    await screen.findByText("AssetOpsBench integration");
    fireEvent.click(screen.getByLabelText("Compose new message"));
    fireEvent.change(await screen.findByLabelText("To"), {
      target: { value: "someone@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "Hi" } });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() =>
      expect(mailApi.sendMessage).toHaveBeenCalledWith("acct-1", {
        to: "someone@example.com",
        subject: "Hi",
        body: "",
        cc: "",
      }),
    );
  });
});
