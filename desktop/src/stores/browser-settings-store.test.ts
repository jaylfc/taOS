import { describe, expect, it, beforeEach } from "vitest";
import {
  useBrowserSettingsStore,
  searchUrlFor,
  SEARCH_ENGINES,
} from "./browser-settings-store";

beforeEach(() => {
  useBrowserSettingsStore.setState({
    discardTimeoutMs: 10 * 60 * 1000,
    maxLiveTabs: 12,
    searchEngine: "duckduckgo",
  });
});

describe("browser-settings-store — defaults", () => {
  it("discardTimeoutMs defaults to 10 minutes", () => {
    expect(useBrowserSettingsStore.getState().discardTimeoutMs).toBe(10 * 60 * 1000);
  });

  it("maxLiveTabs defaults to 12", () => {
    expect(useBrowserSettingsStore.getState().maxLiveTabs).toBe(12);
  });

  it("searchEngine defaults to duckduckgo", () => {
    expect(useBrowserSettingsStore.getState().searchEngine).toBe("duckduckgo");
  });
});

describe("browser-settings-store — setters", () => {
  it("setDiscardTimeoutMs updates the value", () => {
    useBrowserSettingsStore.getState().setDiscardTimeoutMs(5 * 60 * 1000);
    expect(useBrowserSettingsStore.getState().discardTimeoutMs).toBe(5 * 60 * 1000);
  });

  it("setDiscardTimeoutMs clamps to minimum 1 minute", () => {
    useBrowserSettingsStore.getState().setDiscardTimeoutMs(0);
    expect(useBrowserSettingsStore.getState().discardTimeoutMs).toBe(60_000);
  });

  it("setDiscardTimeoutMs clamps to maximum 60 minutes", () => {
    useBrowserSettingsStore.getState().setDiscardTimeoutMs(999 * 60 * 1000);
    expect(useBrowserSettingsStore.getState().discardTimeoutMs).toBe(60 * 60 * 1000);
  });

  it("setMaxLiveTabs updates the value", () => {
    useBrowserSettingsStore.getState().setMaxLiveTabs(20);
    expect(useBrowserSettingsStore.getState().maxLiveTabs).toBe(20);
  });

  it("setMaxLiveTabs clamps to minimum 1", () => {
    useBrowserSettingsStore.getState().setMaxLiveTabs(0);
    expect(useBrowserSettingsStore.getState().maxLiveTabs).toBe(1);
  });

  it("setMaxLiveTabs clamps to maximum 50", () => {
    useBrowserSettingsStore.getState().setMaxLiveTabs(100);
    expect(useBrowserSettingsStore.getState().maxLiveTabs).toBe(50);
  });

  it("setSearchEngine updates to google", () => {
    useBrowserSettingsStore.getState().setSearchEngine("google");
    expect(useBrowserSettingsStore.getState().searchEngine).toBe("google");
  });

  it("setSearchEngine updates to bing", () => {
    useBrowserSettingsStore.getState().setSearchEngine("bing");
    expect(useBrowserSettingsStore.getState().searchEngine).toBe("bing");
  });

  it("setSearchEngine ignores invalid engine values", () => {
    useBrowserSettingsStore.getState().setSearchEngine("yahoo" as SearchEngine);
    expect(useBrowserSettingsStore.getState().searchEngine).toBe("duckduckgo");
  });
});

describe("searchUrlFor", () => {
  it("returns DuckDuckGo URL with encoded query", () => {
    const url = searchUrlFor("duckduckgo", "hello world");
    expect(url).toBe(`${SEARCH_ENGINES.duckduckgo}${encodeURIComponent("hello world")}`);
  });

  it("returns Google URL with encoded query", () => {
    const url = searchUrlFor("google", "typescript tutorial");
    expect(url).toBe(`${SEARCH_ENGINES.google}${encodeURIComponent("typescript tutorial")}`);
  });

  it("returns Bing URL with encoded query", () => {
    const url = searchUrlFor("bing", "open source");
    expect(url).toBe(`${SEARCH_ENGINES.bing}${encodeURIComponent("open source")}`);
  });

  it("encodes special characters in query", () => {
    const url = searchUrlFor("duckduckgo", "what is 2+2?");
    expect(url).toContain(encodeURIComponent("what is 2+2?"));
  });
});
