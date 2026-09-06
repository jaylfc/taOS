import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDragSource } from "../use-drag-source";
import { getCurrent, endDrag } from "../dnd-bus";

describe("useDragSource", () => {
  beforeEach(() => endDrag());

  it("onDragStart calls startDrag on the bus", () => {
    const payload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
    const { result } = renderHook(() => useDragSource({ payload }));
    const setData = vi.fn();
    const e = { dataTransfer: { setData, effectAllowed: "" } } as unknown as React.DragEvent;
    act(() => { result.current.dragHandlers.onDragStart(e); });
    expect(getCurrent()).toEqual(payload);
  });

  it("htmlMirror writes each mime via dataTransfer.setData", () => {
    const payload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
    const { result } = renderHook(() => useDragSource({
      payload,
      htmlMirror: { "text/plain": "/a.txt", "text/uri-list": "https://h/a.txt" },
    }));
    const setData = vi.fn();
    const e = { dataTransfer: { setData, effectAllowed: "" } } as unknown as React.DragEvent;
    act(() => { result.current.dragHandlers.onDragStart(e); });
    expect(setData).toHaveBeenCalledWith("text/plain", "/a.txt");
    expect(setData).toHaveBeenCalledWith("text/uri-list", "https://h/a.txt");
  });

  it("disabled=true sets draggable=false", () => {
    const payload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
    const { result } = renderHook(() => useDragSource({ payload, disabled: true }));
    expect(result.current.dragHandlers.draggable).toBe(false);
  });

  it("onDragEnd clears the bus", () => {
    const payload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
    const { result } = renderHook(() => useDragSource({ payload }));
    const e = { dataTransfer: { setData: vi.fn(), effectAllowed: "" } } as unknown as React.DragEvent;
    act(() => { result.current.dragHandlers.onDragStart(e); });
    expect(getCurrent()).not.toBeNull();
    act(() => { result.current.dragHandlers.onDragEnd(); });
    expect(getCurrent()).toBeNull();
  });
});
