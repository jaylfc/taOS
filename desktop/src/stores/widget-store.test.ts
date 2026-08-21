import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWidgetStore } from "./widget-store";

const originalFetch = global.fetch;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));
  global.fetch = fetchMock as unknown as typeof fetch;
  localStorage.clear();
  useWidgetStore.setState({
    widgets: [],
    showWidgets: false,
    hydrated: true,
  });
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("widget-store — defaults", () => {
  it("starts with showWidgets false and hydrated true after reset", () => {
    expect(useWidgetStore.getState().showWidgets).toBe(false);
    expect(useWidgetStore.getState().hydrated).toBe(true);
  });

  it("starts with an empty widgets list after reset", () => {
    expect(useWidgetStore.getState().widgets).toEqual([]);
  });
});

describe("widget-store — addWidget", () => {
  it("adds a clock widget at position (0,0) with size (3,2)", () => {
    useWidgetStore.getState().addWidget("clock");
    const widgets = useWidgetStore.getState().widgets;
    expect(widgets).toHaveLength(1);
    expect(widgets[0].type).toBe("clock");
    expect(widgets[0].x).toBe(0);
    expect(widgets[0].y).toBe(0);
    expect(widgets[0].w).toBe(3);
    expect(widgets[0].h).toBe(2);
    expect(widgets[0].minW).toBe(2);
    expect(widgets[0].minH).toBe(2);
  });

  it("places a second overlapping widget at a non-overlapping position", () => {
    useWidgetStore.getState().addWidget("agent-status");
    const first = useWidgetStore.getState().widgets[0];
    expect(first.x).toBe(0);
    expect(first.y).toBe(0);
    useWidgetStore.getState().addWidget("agent-status");
    const widgets = useWidgetStore.getState().widgets;
    expect(widgets).toHaveLength(2);
    const second = widgets[1];
    const overlaps =
      second.x < first.x + first.w &&
      second.x + second.w > first.x &&
      second.y < first.y + first.h &&
      second.y + second.h > first.y;
    expect(overlaps).toBe(false);
  });

  it("uses {w:3, h:2} and no minW/minH for an unknown type", () => {
    useWidgetStore.getState().addWidget("unknown-type");
    const widget = useWidgetStore.getState().widgets[0];
    expect(widget.w).toBe(3);
    expect(widget.h).toBe(2);
    expect(widget.minW).toBeUndefined();
    expect(widget.minH).toBeUndefined();
  });
});

describe("widget-store — removeWidget", () => {
  it("removes the widget with the given id", () => {
    useWidgetStore.getState().addWidget("clock");
    const id = useWidgetStore.getState().widgets[0].id;
    useWidgetStore.getState().removeWidget(id);
    expect(useWidgetStore.getState().widgets).toHaveLength(0);
  });

  it("does nothing when removing an id that is not present", () => {
    useWidgetStore.getState().addWidget("clock");
    useWidgetStore.getState().removeWidget("nonexistent");
    expect(useWidgetStore.getState().widgets).toHaveLength(1);
  });
});

describe("widget-store — updateLayout", () => {
  it("updates position and size for a matching widget id", () => {
    useWidgetStore.getState().addWidget("clock");
    const [{ id }] = useWidgetStore.getState().widgets;
    useWidgetStore.getState().updateLayout([{ id, x: 5, y: 3, w: 6, h: 4 }]);
    const updated = useWidgetStore.getState().widgets[0];
    expect(updated.x).toBe(5);
    expect(updated.y).toBe(3);
    expect(updated.w).toBe(6);
    expect(updated.h).toBe(4);
  });

  it("leaves widgets unchanged when no layout entry matches", () => {
    useWidgetStore.getState().addWidget("clock");
    const original = useWidgetStore.getState().widgets[0];
    useWidgetStore.getState().updateLayout([{ id: "other", x: 9, y: 9, w: 1, h: 1 }]);
    const after = useWidgetStore.getState().widgets[0];
    expect(after.x).toBe(original.x);
    expect(after.y).toBe(original.y);
    expect(after.w).toBe(original.w);
    expect(after.h).toBe(original.h);
  });
});

describe("widget-store — toggleWidgets", () => {
  it("sets showWidgets to true when it was false", () => {
    useWidgetStore.getState().toggleWidgets();
    expect(useWidgetStore.getState().showWidgets).toBe(true);
  });

  it("sets showWidgets to false when it was true", () => {
    useWidgetStore.setState({ showWidgets: true });
    useWidgetStore.getState().toggleWidgets();
    expect(useWidgetStore.getState().showWidgets).toBe(false);
  });
});
