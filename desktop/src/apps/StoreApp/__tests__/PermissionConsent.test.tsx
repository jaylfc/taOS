import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PermissionConsent, SENSITIVE_CAPS } from "../PermissionConsent";

describe("PermissionConsent", () => {
  it("shows sensitive caps as toggles defaulting OFF and free caps as always-allowed", () => {
    render(<PermissionConsent appName="Todo" requested={["app.net", "app.kv"]} onConfirm={() => {}} onCancel={() => {}} />);
    const net = screen.getByLabelText(/network/i) as HTMLInputElement;
    expect(net.checked).toBe(false);             // sensitive, default off
    expect(SENSITIVE_CAPS.has("app.net")).toBe(true);
    // free cap not shown as a toggle
    expect(screen.queryByLabelText(/^app\.kv/i)).toBeNull();
  });

  it("confirms with only the toggled-on sensitive caps", () => {
    const onConfirm = vi.fn();
    render(<PermissionConsent appName="Todo" requested={["app.net", "app.memory"]} onConfirm={onConfirm} onCancel={() => {}} />);
    fireEvent.click(screen.getByLabelText(/network/i));
    fireEvent.click(screen.getByText(/install/i));
    expect(onConfirm).toHaveBeenCalledWith(["app.net"]);
  });
});
