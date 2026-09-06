import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDropTarget } from "../use-drop-target";
import { startDrag, endDrag } from "../dnd-bus";

const filePayload = { kind: "file" as const, path: "/a.txt", mime_type: "text/plain", size: 10, name: "a.txt" };
const msgPayload = { kind: "message" as const, channel_id: "c1", message_id: "m1", author_id: "tom", excerpt: "hi" };

describe("useDropTarget", () => {
  beforeEach(() => endDrag());

  it("isValidTarget true when bus payload matches accept", () => {
    const { result, rerender } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop: vi.fn() }),
    );
    expect(result.current.isValidTarget).toBe(false);
    act(() => { startDrag(filePayload); });
    rerender();
    expect(result.current.isValidTarget).toBe(true);
  });

  it("isValidTarget false when bus payload type not accepted", () => {
    const { result, rerender } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop: vi.fn() }),
    );
    act(() => { startDrag(msgPayload); });
    rerender();
    expect(result.current.isValidTarget).toBe(false);
  });

  it("onDrop callback fires with payload when valid", () => {
    const onDrop = vi.fn();
    const { result } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop }),
    );
    act(() => { startDrag(filePayload); });
    const e = { preventDefault: vi.fn() } as unknown as React.DragEvent;
    act(() => { result.current.dropHandlers.onDrop(e); });
    expect(e.preventDefault).toHaveBeenCalled();
    expect(onDrop).toHaveBeenCalledWith(filePayload);
  });

  it("onDrop callback does NOT fire when invalid type", () => {
    const onDrop = vi.fn();
    const { result } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop }),
    );
    act(() => { startDrag(msgPayload); });
    const e = { preventDefault: vi.fn() } as unknown as React.DragEvent;
    act(() => { result.current.dropHandlers.onDrop(e); });
    expect(onDrop).not.toHaveBeenCalled();
  });

  it("isOver tracks enter/leave counter for nested children", () => {
    const { result, rerender } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop: vi.fn() }),
    );
    // isOver/preventDefault only react while a valid drag is in flight.
    act(() => { startDrag(filePayload); });
    rerender();
    const enter = { preventDefault: vi.fn() } as unknown as React.DragEvent;
    act(() => { result.current.dropHandlers.onDragEnter(enter); });
    rerender();
    expect(result.current.isOver).toBe(true);
    act(() => { result.current.dropHandlers.onDragEnter(enter); });
    act(() => { result.current.dropHandlers.onDragLeave(enter); });
    rerender();
    expect(result.current.isOver).toBe(true);
    act(() => { result.current.dropHandlers.onDragLeave(enter); });
    rerender();
    expect(result.current.isOver).toBe(false);
  });

  it("isOver stays false when drag payload type is not accepted", () => {
    const { result, rerender } = renderHook(() =>
      useDropTarget({ accept: ["file"], onDrop: vi.fn() }),
    );
    act(() => { startDrag(msgPayload); });
    rerender();
    const enter = { preventDefault: vi.fn() } as unknown as React.DragEvent;
    act(() => { result.current.dropHandlers.onDragEnter(enter); });
    rerender();
    expect(result.current.isOver).toBe(false);
    expect(enter.preventDefault).not.toHaveBeenCalled();
  });
});
