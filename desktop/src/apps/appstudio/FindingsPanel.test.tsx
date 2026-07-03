import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { FindingsPanel } from "./FindingsPanel";
import type { Finding } from "./analyze-source";

afterEach(cleanup);

const CRITICAL_FINDING: Finding = {
  severity: "critical",
  rule_id: "eval-like-execution",
  file: "app.js",
  line: 12,
  message: "Use of eval() executes arbitrary strings as code.",
};

const WARNING_FINDING: Finding = {
  severity: "warning",
  rule_id: "some-warning-rule",
  file: "app.js",
  line: 3,
  message: "A non-blocking warning finding.",
};

describe("FindingsPanel", () => {
  it("shows a loading state while scanning", () => {
    render(<FindingsPanel loading={true} error={null} findings={[]} />);
    expect(screen.getByTestId("security-findings-loading")).toBeInTheDocument();
  });

  it("shows an error state when the scan request fails", () => {
    render(<FindingsPanel loading={false} error="network down" findings={[]} />);
    expect(screen.getByTestId("security-findings-error")).toBeInTheDocument();
    expect(screen.getByText(/network down/i)).toBeInTheDocument();
  });

  it("shows a clean state when there are no findings", () => {
    render(<FindingsPanel loading={false} error={null} findings={[]} />);
    expect(screen.getByTestId("security-findings-clean")).toBeInTheDocument();
    expect(screen.getByText(/no security issues found/i)).toBeInTheDocument();
  });

  it("shows a blocked banner when a critical finding is present", () => {
    render(<FindingsPanel loading={false} error={null} findings={[CRITICAL_FINDING]} />);
    expect(screen.getByTestId("security-findings-blocked")).toBeInTheDocument();
    expect(screen.getByText(/publish is blocked/i)).toBeInTheDocument();
  });

  it("renders each finding with file:line and message", () => {
    render(<FindingsPanel loading={false} error={null} findings={[CRITICAL_FINDING]} />);
    expect(screen.getByText("app.js:12")).toBeInTheDocument();
    expect(screen.getByText(CRITICAL_FINDING.message)).toBeInTheDocument();
  });

  it("does not show a blocked banner for warnings-only findings", () => {
    render(<FindingsPanel loading={false} error={null} findings={[WARNING_FINDING]} />);
    expect(screen.queryByTestId("security-findings-blocked")).not.toBeInTheDocument();
    expect(screen.getByText(WARNING_FINDING.message)).toBeInTheDocument();
  });

  it("renders both critical and warning findings together", () => {
    render(
      <FindingsPanel loading={false} error={null} findings={[WARNING_FINDING, CRITICAL_FINDING]} />,
    );
    expect(screen.getAllByTestId("finding-critical").length).toBe(1);
    expect(screen.getAllByTestId("finding-warning").length).toBe(1);
  });
});
