import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { LaunchpadIcon } from "../LaunchpadIcon";

vi.mock("@/registry/app-registry", () => ({
  prefetchApp: vi.fn(),
}));

const minimalApp = {
  id: "messages",
  name: "Messages",
  icon: "message-circle",
  category: "platform" as const,
  component: () => Promise.resolve({ default: () => null }),
  defaultSize: { w: 900, h: 600 },
  minSize: { w: 400, h: 300 },
  singleton: true,
  pinned: true,
  launchpadOrder: 1,
};

describe("<LaunchpadIcon />", () => {
  it("renders the app name", () => {
    render(<LaunchpadIcon app={minimalApp} onClick={vi.fn()} />);
    expect(screen.getByText("Messages")).toBeInTheDocument();
  });

  it("has a button labelled with the app name", () => {
    render(<LaunchpadIcon app={minimalApp} onClick={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: /open messages/i }),
    ).toBeInTheDocument();
  });

  it("calls onClick when the button is clicked", () => {
    const onClick = vi.fn();
    render(<LaunchpadIcon app={minimalApp} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: /open messages/i }));
    expect(onClick).toHaveBeenCalled();
  });
});
