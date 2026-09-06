import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

/* Tone.js and smplr both require a real AudioContext, which jsdom does not
 * implement -- mock both so the Studio Engine can mount without them.
 * (Kept in sync with audio-engine.test.ts's mock.) */
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

import { MusicStudioApp } from "./MusicStudioApp";

function renderApp() {
  return render(<MusicStudioApp windowId="test-window" />);
}

describe("MusicStudioApp", () => {
  it("renders all rail items", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Music Studio views" });
    expect(nav).toBeDefined();
    expect(screen.getByRole("button", { name: "Studio" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Compose" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Sounds" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Mixer" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Export" })).toBeDefined();
  });

  it("shows Studio view by default with Studio rail item active", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Music Studio views" });
    const studioBtn = nav.querySelector('[aria-label="Studio"]') as HTMLElement;
    expect(studioBtn).toBeTruthy();
    expect(studioBtn.getAttribute("aria-current")).toBe("page");
  });

  it("Studio view shows transport controls", () => {
    renderApp();
    expect(screen.getByRole("button", { name: "Stop" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Play" })).toBeDefined();
  });

  it("Studio view shows a track in the track list", () => {
    renderApp();
    expect(screen.getAllByText("Drums").length).toBeGreaterThan(0);
  });

  it("switches to Compose view on rail click", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Compose" }));
    const nav = screen.getByRole("navigation", { name: "Music Studio views" });
    const composeBtn = nav.querySelector('[aria-label="Compose"]') as HTMLElement;
    expect(composeBtn.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "Compose" })).toBeDefined();
  });

  it("Compose view shows Generate button and style chips", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Compose" }));
    expect(screen.getByRole("button", { name: "Generate" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Lo-fi" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Cinematic" })).toBeDefined();
  });

  it("switches to Sounds view on rail click", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Sounds" }));
    const nav = screen.getByRole("navigation", { name: "Music Studio views" });
    const soundsBtn = nav.querySelector('[aria-label="Sounds"]') as HTMLElement;
    expect(soundsBtn.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "Sounds" })).toBeDefined();
  });

  it("Sounds view shows filter pills and instrument cards", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Sounds" }));
    expect(screen.getByRole("button", { name: "All" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Drums" })).toBeDefined();
    expect(screen.getByText("Boom Bap Kit")).toBeDefined();
    expect(screen.getByText("Rhodes Mk I")).toBeDefined();
  });
});
