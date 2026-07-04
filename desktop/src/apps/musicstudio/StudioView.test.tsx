import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";

/* Tone.js and smplr both require a real AudioContext, which jsdom does not
 * implement -- mock both so useStudioEngine's wiring can run without them.
 * (Kept in sync with audio-engine.test.ts's mock; vi.mock factories can't
 * import a shared helper because vi.mock calls are hoisted above imports.) */
vi.mock("tone", () => {
  class FakeChannel {
    volume: { value: number };
    pan: { value: number };
    mute: boolean;
    solo: boolean;
    constructor(opts: { volume?: number; pan?: number; mute?: boolean; solo?: boolean } = {}) {
      this.volume = { value: opts.volume ?? 0 };
      this.pan = { value: opts.pan ?? 0 };
      this.mute = opts.mute ?? false;
      this.solo = opts.solo ?? false;
    }
    connect() {
      return this;
    }
    toDestination() {
      return this;
    }
    dispose() {}
  }
  class FakeSynth {
    constructor(_opts?: unknown) {}
    connect() {
      return this;
    }
    triggerAttackRelease() {}
    dispose() {}
  }
  let idCounter = 0;
  const transport = {
    bpm: { value: 120 },
    position: "0:0:0" as string | number,
    state: "stopped" as "stopped" | "started",
    schedule: vi.fn(() => {
      idCounter += 1;
      return idCounter;
    }),
    clear: vi.fn(),
    cancel: vi.fn(),
    start: vi.fn(() => {
      transport.state = "started";
    }),
    stop: vi.fn(() => {
      transport.state = "stopped";
    }),
  };
  return {
    start: vi.fn(async () => {}),
    now: vi.fn(() => 0),
    getTransport: () => transport,
    getContext: () => ({ rawContext: { createGain: () => ({ connect() {}, disconnect() {} }) } }),
    getDestination: () => ({ volume: { value: 0 } }),
    connect: vi.fn(),
    gainToDb: (gain: number) => (gain <= 0 ? -Infinity : 20 * Math.log10(gain)),
    Midi: (pitch: number) => ({ toFrequency: () => 440 * 2 ** ((pitch - 69) / 12) }),
    Channel: FakeChannel,
    MembraneSynth: FakeSynth,
    NoiseSynth: FakeSynth,
    MonoSynth: FakeSynth,
    PolySynth: FakeSynth,
    AMSynth: FakeSynth,
    Synth: FakeSynth,
  };
});

vi.mock("smplr", () => ({
  SplendidGrandPiano: () => ({ ready: Promise.resolve(), start: vi.fn(), dispose: vi.fn() }),
  Soundfont: () => ({ ready: Promise.resolve(), start: vi.fn(), dispose: vi.fn() }),
}));

import { StudioView } from "./StudioView";
import { useStudioEngine } from "./use-studio-engine";

function Harness() {
  const engine = useStudioEngine();
  return <StudioView engine={engine} onSave={() => {}} saving={false} saveStatus="idle" />;
}

describe("StudioView", () => {
  it("adding a track increases the track list", () => {
    render(<Harness />);

    const tracksBefore = screen.getAllByLabelText(/^Mute /).length;
    fireEvent.click(screen.getByLabelText("Add track"));
    const tracksAfter = screen.getAllByLabelText(/^Mute /).length;

    expect(tracksAfter).toBe(tracksBefore + 1);
  });

  it("clicking an empty piano-roll cell adds a note to the selected clip", () => {
    render(<Harness />);

    // The default seed selects the Drums track (which uses the step
    // sequencer, not the piano roll) -- switch to Bass, a melodic track.
    fireEvent.click(screen.getByText("Bass"));

    const grid = screen.getByRole("grid", { name: /Piano roll for/i });
    const notesBefore = within(grid).getAllByRole("button", { name: /^Note /i }).length;

    // Column 5 (empty), a pitch (A3=57) not used by the seeded bassline.
    fireEvent.click(grid, { clientX: 5 * 36 + 2, clientY: (96 - 57) * 16 + 2 });

    const notesAfter = within(grid).getAllByRole("button", { name: /^Note /i }).length;
    expect(notesAfter).toBe(notesBefore + 1);
  });

  it("clicking an existing note SELECTS it (does not delete)", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("Bass"));

    const grid = screen.getByRole("grid", { name: /Piano roll for/i });
    const before = within(grid).getAllByRole("button", { name: /^Note /i }).length;
    expect(before).toBeGreaterThan(0);

    const firstNote = within(grid).getAllByRole("button", { name: /^Note /i })[0];
    fireEvent.pointerDown(firstNote);
    fireEvent.pointerUp(window);

    // Single click is non-destructive: the note count is unchanged and the
    // clicked note is now selected.
    const after = within(grid).getAllByRole("button", { name: /^Note /i });
    expect(after).toHaveLength(before);
    expect(after[0]).toHaveAttribute("aria-pressed", "true");
  });

  it("deletes the selected note only on an explicit Delete key press", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("Bass"));

    const grid = screen.getByRole("grid", { name: /Piano roll for/i });
    const before = within(grid).getAllByRole("button", { name: /^Note /i }).length;
    expect(before).toBeGreaterThan(0);

    // Select the first note (click is non-destructive), then Delete removes it.
    const firstNote = within(grid).getAllByRole("button", { name: /^Note /i })[0];
    fireEvent.pointerDown(firstNote);
    fireEvent.pointerUp(window);
    fireEvent.keyDown(grid, { key: "Delete" });

    const after = within(grid).getAllByRole("button", { name: /^Note /i }).length;
    expect(after).toBe(before - 1);
  });

  it("toggling a step sequencer cell adds a note for the drum track", () => {
    render(<Harness />);
    // Drums is selected by default -- the step sequencer should be visible.
    const step = screen.getByLabelText("Kick step 2");
    expect(step).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(step);
    expect(step).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(step);
    expect(step).toHaveAttribute("aria-pressed", "false");
  });
});
