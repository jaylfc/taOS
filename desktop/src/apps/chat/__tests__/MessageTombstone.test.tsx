import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageTombstone } from "../MessageTombstone";

describe("MessageTombstone", () => {
  it("renders deleted message notice", () => {
    render(<MessageTombstone />);
    expect(screen.getByText(/deleted/i)).toBeInTheDocument();
  });
});
