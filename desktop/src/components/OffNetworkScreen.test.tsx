import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OffNetworkScreen } from "./OffNetworkScreen";

describe("OffNetworkScreen", () => {
  it("renders the unreachable message and the taOSgo call to action", () => {
    render(<OffNetworkScreen onRetry={vi.fn()} />);
    expect(screen.getByText("Can't reach your taOS")).toBeInTheDocument();
    const cta = screen.getByRole("link", { name: /get taosgo/i });
    expect(cta).toHaveAttribute("href", "https://taos.my/taosgo");
  });

  it("calls onRetry when Try again is clicked", async () => {
    const onRetry = vi.fn().mockResolvedValue(undefined);
    render(<OffNetworkScreen onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(onRetry).toHaveBeenCalledTimes(1));
  });
});
