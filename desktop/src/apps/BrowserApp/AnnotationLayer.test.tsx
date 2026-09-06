import { describe, it, expect, beforeEach } from "vitest";
import { render, act } from "@testing-library/react";
import { AnnotationLayer } from "./AnnotationLayer";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";

const WINDOW_ID = "win-1";
const TAB_ID = "tab-1";

beforeEach(() => {
  useBrowserAgentStore.setState({
    panels: {},
    lastEventAt: {},
    messages: {},
    recentEvents: {},
    annotations: {},
  });
});

describe("AnnotationLayer", () => {
  it("renders an empty SVG when no annotations", () => {
    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(container.querySelector("polygon")).toBeNull();
    expect(container.querySelector("line")).toBeNull();
  });

  it("renders a cursor as a polygon at the right position", () => {
    useBrowserAgentStore.getState().addAnnotation(WINDOW_ID, TAB_ID, {
      kind: "cursor",
      id: "c-1",
      agentId: "agent-a",
      x: 100,
      y: 200,
    });

    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    const polygon = container.querySelector("polygon");
    expect(polygon).toBeTruthy();
    // The points attribute should encode the x,y origin
    expect(polygon!.getAttribute("points")).toContain("100,200");
  });

  it("renders the cursor's label as text", () => {
    useBrowserAgentStore.getState().addAnnotation(WINDOW_ID, TAB_ID, {
      kind: "cursor",
      id: "c-1",
      agentId: "agent-a",
      x: 50,
      y: 80,
      label: "Agent A",
    });

    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    const text = container.querySelector("text");
    expect(text).toBeTruthy();
    expect(text!.textContent).toBe("Agent A");
  });

  it("renders an arrow as a line between two points", () => {
    useBrowserAgentStore.getState().addAnnotation(WINDOW_ID, TAB_ID, {
      kind: "arrow",
      id: "arr-1",
      agentId: "agent-a",
      from: { x: 10, y: 20 },
      to: { x: 100, y: 200 },
    });

    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    const line = container.querySelector("line");
    expect(line).toBeTruthy();
    expect(line!.getAttribute("x1")).toBe("10");
    expect(line!.getAttribute("y1")).toBe("20");
    expect(line!.getAttribute("x2")).toBe("100");
    expect(line!.getAttribute("y2")).toBe("200");
  });

  it("uses provided colors", () => {
    useBrowserAgentStore.getState().addAnnotation(WINDOW_ID, TAB_ID, {
      kind: "cursor",
      id: "c-1",
      agentId: "agent-a",
      x: 10,
      y: 10,
      color: "#ff0000",
    });

    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    const polygon = container.querySelector("polygon");
    expect(polygon!.getAttribute("fill")).toBe("#ff0000");
  });

  it("falls back to default color when not specified", () => {
    useBrowserAgentStore.getState().addAnnotation(WINDOW_ID, TAB_ID, {
      kind: "cursor",
      id: "c-1",
      agentId: "agent-a",
      x: 10,
      y: 10,
    });

    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    const polygon = container.querySelector("polygon");
    expect(polygon!.getAttribute("fill")).toBe("#6c8df0");
  });

  it("uses pointer-events: none so it doesn't block iframe", () => {
    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    const svg = container.querySelector("svg");
    expect(svg!.style.pointerEvents).toBe("none");
  });

  it("re-renders when addAnnotation is called", () => {
    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    expect(container.querySelector("polygon")).toBeNull();

    act(() => {
      useBrowserAgentStore.getState().addAnnotation(WINDOW_ID, TAB_ID, {
        kind: "cursor",
        id: "c-1",
        agentId: "agent-a",
        x: 50,
        y: 50,
      });
    });

    expect(container.querySelector("polygon")).toBeTruthy();
  });

  it("removes annotation when clearAnnotation is called", () => {
    useBrowserAgentStore.getState().addAnnotation(WINDOW_ID, TAB_ID, {
      kind: "cursor",
      id: "c-1",
      agentId: "agent-a",
      x: 50,
      y: 50,
    });

    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    expect(container.querySelector("polygon")).toBeTruthy();

    act(() => {
      useBrowserAgentStore.getState().clearAnnotation(WINDOW_ID, TAB_ID, "c-1");
    });

    expect(container.querySelector("polygon")).toBeNull();
  });

  it("clearAnnotations removes all for the (window, tab)", () => {
    const s = useBrowserAgentStore.getState();
    s.addAnnotation(WINDOW_ID, TAB_ID, { kind: "cursor", id: "c-1", agentId: "agent-a", x: 10, y: 10 });
    s.addAnnotation(WINDOW_ID, TAB_ID, { kind: "cursor", id: "c-2", agentId: "agent-b", x: 20, y: 20 });

    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    expect(container.querySelectorAll("polygon")).toHaveLength(2);

    act(() => {
      useBrowserAgentStore.getState().clearAnnotations(WINDOW_ID, TAB_ID);
    });

    expect(container.querySelectorAll("polygon")).toHaveLength(0);
  });

  it("clearAnnotations(agentId) removes only that agent's annotations", () => {
    const s = useBrowserAgentStore.getState();
    s.addAnnotation(WINDOW_ID, TAB_ID, { kind: "cursor", id: "c-1", agentId: "agent-a", x: 10, y: 10 });
    s.addAnnotation(WINDOW_ID, TAB_ID, { kind: "cursor", id: "c-2", agentId: "agent-b", x: 20, y: 20 });

    const { container } = render(
      <AnnotationLayer windowId={WINDOW_ID} tabId={TAB_ID} />,
    );

    expect(container.querySelectorAll("polygon")).toHaveLength(2);

    act(() => {
      useBrowserAgentStore.getState().clearAnnotations(WINDOW_ID, TAB_ID, "agent-a");
    });

    expect(container.querySelectorAll("polygon")).toHaveLength(1);
  });
});
