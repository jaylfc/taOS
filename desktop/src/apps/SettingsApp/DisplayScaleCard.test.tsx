import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DisplayScaleCard } from "./DisplayScaleCard";
import { useDisplayStore, DEFAULT_SCALE } from "@/stores/display-store";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("style");
  useDisplayStore.setState({ uiScale: DEFAULT_SCALE });
});

describe("DisplayScaleCard", () => {
  it("renders every scale step as a radio so keyboard and screen readers get platform behaviour", () => {
    render(<DisplayScaleCard />);
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(5);
  });

  it("marks the current scale as checked", () => {
    render(<DisplayScaleCard />);
    expect(screen.getByRole("radio", { name: /100%/ })).toBeChecked();
  });

  it("changes the scale when another step is picked", () => {
    render(<DisplayScaleCard />);
    fireEvent.click(screen.getByRole("radio", { name: /80%/ }));
    expect(useDisplayStore.getState().uiScale).toBe(0.8);
    expect(document.documentElement.style.zoom).toBe("0.8");
  });

  it("tells the user the setting is device-local, since that is surprising", () => {
    render(<DisplayScaleCard />);
    expect(screen.getByText(/this device only/i)).toBeInTheDocument();
  });

  it("keeps the decorative end captions out of the accessible name", () => {
    render(<DisplayScaleCard />);
    // "More Space" is aria-hidden, so the radio's name is the percentage alone.
    const smallest = screen.getByRole("radio", { name: "80%" });
    expect(smallest).toBeInTheDocument();
  });
});
