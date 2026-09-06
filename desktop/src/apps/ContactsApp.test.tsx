import { render, screen, fireEvent, within, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ContactsApp } from "./ContactsApp";

vi.mock("@/components/ui", () => ({
  Button: ({
    children,
    onClick,
    "aria-label": ariaLabel,
    variant,
    size,
    disabled,
    className,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    "aria-label"?: string;
    variant?: string;
    size?: string;
    disabled?: boolean;
    className?: string;
  }) => (
    <button onClick={onClick} aria-label={ariaLabel} disabled={disabled} className={className}>
      {children}
    </button>
  ),
  Input: ({ value, onChange, placeholder, "aria-label": ariaLabel, id, type, autoFocus, className }: {
    value: string;
    onChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
    placeholder?: string;
    "aria-label"?: string;
    id?: string;
    type?: string;
    autoFocus?: boolean;
    className?: string;
  }) => (
    <input
      value={value}
      placeholder={placeholder}
      aria-label={ariaLabel}
      id={id}
      type={type}
      autoFocus={autoFocus}
      className={className}
      onChange={(e) => onChange?.(e)}
    />
  ),
  Textarea: ({ value, onChange, placeholder, "aria-label": ariaLabel, className }: {
    value: string;
    onChange?: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
    placeholder?: string;
    "aria-label"?: string;
    className?: string;
  }) => (
    <textarea
      value={value}
      placeholder={placeholder}
      aria-label={ariaLabel}
      className={className}
      onChange={(e) => onChange?.(e)}
    />
  ),
  Label: ({ children, htmlFor }: { children: React.ReactNode; htmlFor?: string }) => (
    <label htmlFor={htmlFor}>{children}</label>
  ),
  Card: ({ children, className, style }: { children: React.ReactNode; className?: string; style?: React.CSSProperties }) => (
    <div className={className} style={style}>{children}</div>
  ),
  CardContent: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));

vi.mock("@/components/mobile/MobileSplitView", () => ({
  MobileSplitView: ({
    list,
    detail,
    listTitle,
    detailTitle,
  }: {
    list: React.ReactNode;
    detail: React.ReactNode;
    listTitle?: string;
    detailTitle?: string;
  }) => (
    <div>
      <div data-testid="split-list" aria-label={listTitle}>{list}</div>
      <div data-testid="split-detail" aria-label={detailTitle ?? ""}>{detail}</div>
    </div>
  ),
}));

vi.mock("@/hooks/use-is-mobile", () => ({
  useIsMobile: () => false,
}));

function clickAddContact() {
  fireEvent.click(screen.getAllByRole("button", { name: "Add contact" })[0]);
}

function flush() {
  return act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("ContactsApp", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("renders the Contacts toolbar with an empty count", () => {
    render(<ContactsApp windowId="win-1" />);
    expect(screen.getByRole("heading", { name: "Contacts" })).toBeInTheDocument();
    expect(screen.getByText("0 contacts")).toBeInTheDocument();
  });

  it("shows an empty state when there are no contacts", () => {
    render(<ContactsApp windowId="win-1" />);
    expect(screen.getByText("No contacts yet")).toBeInTheDocument();
  });

  it("does not fetch on mount (no API call is made)", async () => {
    render(<ContactsApp windowId="win-1" />);
    await flush();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("opens the add-contact form when Add Contact is clicked", () => {
    render(<ContactsApp windowId="win-1" />);
    clickAddContact();
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Phone")).toBeInTheDocument();
    expect(screen.getByLabelText("Notes")).toBeInTheDocument();
  });

  it("creates a new contact via the form and shows it in the list", () => {
    render(<ContactsApp windowId="win-1" />);
    clickAddContact();

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ada Lovelace" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Phone"), { target: { value: "+1 555 0100" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const list = screen.getByTestId("split-list");
    expect(within(list).getByText("Ada Lovelace")).toBeInTheDocument();
    expect(within(list).getByText("ada@example.com")).toBeInTheDocument();
  });

  it("disables Save until a name is entered", () => {
    render(<ContactsApp windowId="win-1" />);
    clickAddContact();
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Grace" } });
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("cancels the form without adding a contact", () => {
    render(<ContactsApp windowId="win-1" />);
    clickAddContact();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(screen.getByText("0 contacts")).toBeInTheDocument();
  });

  it("selects a contact and shows its details", () => {
    render(<ContactsApp windowId="win-1" />);
    clickAddContact();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Alan Turing" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alan@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    fireEvent.click(within(screen.getByTestId("split-list")).getByText("Alan Turing"));

    const detail = screen.getByTestId("split-detail");
    expect(within(detail).getByText("Email")).toBeInTheDocument();
    expect(within(detail).getByText("alan@example.com")).toBeInTheDocument();
  });

  it("edits an existing contact and persists the change", () => {
    render(<ContactsApp windowId="win-1" />);
    clickAddContact();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Lin Wei" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    fireEvent.click(within(screen.getByTestId("split-list")).getByText("Lin Wei"));
    fireEvent.click(screen.getByRole("button", { name: "Edit Lin Wei" }));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Lin Wei Jr" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const list = screen.getByTestId("split-list");
    expect(within(list).getByText("Lin Wei Jr")).toBeInTheDocument();
    expect(within(list).queryByText("Lin Wei")).not.toBeInTheDocument();
  });

  it("deletes a contact from the list", () => {
    render(<ContactsApp windowId="win-1" />);
    clickAddContact();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Katherine Johnson" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    fireEvent.click(within(screen.getByTestId("split-list")).getByText("Katherine Johnson"));
    fireEvent.click(screen.getByRole("button", { name: "Delete Katherine Johnson" }));

    const list = screen.getByTestId("split-list");
    expect(within(list).queryByText("Katherine Johnson")).not.toBeInTheDocument();
    expect(screen.getByText("0 contacts")).toBeInTheDocument();
  });

  it("filters the list by the search query", () => {
    render(<ContactsApp windowId="win-1" />);

    clickAddContact();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Margaret Hamilton" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    clickAddContact();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Edsger Dijkstra" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    fireEvent.change(screen.getByLabelText("Search contacts"), {
      target: { value: "dijkstra" },
    });

    const list = screen.getByTestId("split-list");
    expect(within(list).getByText("Edsger Dijkstra")).toBeInTheDocument();
    expect(within(list).queryByText("Margaret Hamilton")).not.toBeInTheDocument();
  });
});
