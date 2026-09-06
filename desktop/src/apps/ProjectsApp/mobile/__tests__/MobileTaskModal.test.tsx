import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MobileTaskModal } from "../MobileTaskModal";

const baseTask = {
  id: "t1",
  title: "Ship it",
  description: "Test description",
  status: "open",
  priority: 2,
  assignee_id: null,
  labels: [],
  parent_task_id: null,
};

const noopHandlers = {
  onClose: vi.fn(),
  onPrev: vi.fn(),
  onNext: vi.fn(),
  onChangeStatus: vi.fn(),
  hasPrev: false,
  hasNext: false,
};

describe("MobileTaskModal", () => {
  it("renders all five sections in order", () => {
    render(<MobileTaskModal task={baseTask} {...noopHandlers} />);
    const sections = screen.getAllByRole("region");
    const labels = sections.map((s) => s.getAttribute("aria-label"));
    expect(labels).toEqual(["Hero", "Metadata", "SubTasks", "Relationships", "Activity"]);
  });

  it("calls onClose when the close button is tapped", () => {
    const onClose = vi.fn();
    render(<MobileTaskModal task={baseTask} {...noopHandlers} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows a sticky action button for status change", () => {
    render(<MobileTaskModal task={baseTask} {...noopHandlers} />);
    expect(screen.getByRole("button", { name: /claim/i })).toBeInTheDocument();
  });

  it("the Activity section is collapsed by default", () => {
    render(<MobileTaskModal task={baseTask} {...noopHandlers} />);
    const activity = screen.getByRole("region", { name: "Activity" });
    expect(activity.querySelector("details")).not.toHaveAttribute("open");
  });

  it.each([
    ["open" as const, /^Claim$/, "claimed"],
    ["claimed" as const, /^Close$/, "closed"],
    ["closed" as const, /^Reopen$/, "open"],
    ["blocked" as const, /^Update$/, "blocked"],
  ])("status %s shows %s and emits %s", (status, label, next) => {
    const onChangeStatus = vi.fn();
    render(
      <MobileTaskModal
        task={{ ...baseTask, status }}
        {...noopHandlers}
        onChangeStatus={onChangeStatus}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(onChangeStatus).toHaveBeenCalledWith(next);
  });
});

describe("MobileTaskModal — swipe gestures", () => {
  it("calls onNext when the user swipes left", () => {
    const onNext = vi.fn();
    render(<MobileTaskModal task={baseTask} {...noopHandlers} hasNext onNext={onNext} />);
    const dialog = screen.getByTestId("mobile-task-modal");
    fireEvent.touchStart(dialog, { touches: [{ clientX: 300, clientY: 100 }] });
    fireEvent.touchEnd(dialog, { changedTouches: [{ clientX: 80, clientY: 100 }] });
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("calls onPrev when the user swipes right", () => {
    const onPrev = vi.fn();
    render(<MobileTaskModal task={baseTask} {...noopHandlers} hasPrev onPrev={onPrev} />);
    const dialog = screen.getByTestId("mobile-task-modal");
    fireEvent.touchStart(dialog, { touches: [{ clientX: 80, clientY: 100 }] });
    fireEvent.touchEnd(dialog, { changedTouches: [{ clientX: 300, clientY: 100 }] });
    expect(onPrev).toHaveBeenCalledOnce();
  });

  it("ignores small touches (< 60px threshold)", () => {
    const onNext = vi.fn();
    render(<MobileTaskModal task={baseTask} {...noopHandlers} hasNext onNext={onNext} />);
    const dialog = screen.getByTestId("mobile-task-modal");
    fireEvent.touchStart(dialog, { touches: [{ clientX: 100, clientY: 100 }] });
    fireEvent.touchEnd(dialog, { changedTouches: [{ clientX: 90, clientY: 100 }] });
    expect(onNext).not.toHaveBeenCalled();
  });

  it("ignores vertical-dominant swipes", () => {
    const onNext = vi.fn();
    render(<MobileTaskModal task={baseTask} {...noopHandlers} hasNext onNext={onNext} />);
    const dialog = screen.getByTestId("mobile-task-modal");
    // |dx|=80 (clears threshold), |dy|=160 (dy > |dx| → vertical-dominant guard fires)
    fireEvent.touchStart(dialog, { touches: [{ clientX: 150, clientY: 100 }] });
    fireEvent.touchEnd(dialog, { changedTouches: [{ clientX: 230, clientY: 260 }] });
    expect(onNext).not.toHaveBeenCalled();
  });

  it("ignores multi-touch (pinch) gestures", () => {
    const onNext = vi.fn();
    render(<MobileTaskModal task={baseTask} {...noopHandlers} hasNext onNext={onNext} />);
    const dialog = screen.getByTestId("mobile-task-modal");
    // touchstart with TWO fingers — first one near left, second near right
    fireEvent.touchStart(dialog, {
      touches: [
        { clientX: 100, clientY: 100 },
        { clientX: 300, clientY: 100 },
      ],
    });
    // first finger lifts after sliding right (large dx if guard absent)
    fireEvent.touchEnd(dialog, { changedTouches: [{ clientX: 300, clientY: 100 }] });
    expect(onNext).not.toHaveBeenCalled();
  });
});
