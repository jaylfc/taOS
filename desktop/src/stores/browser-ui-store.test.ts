import { describe, expect, it, beforeEach } from "vitest";
import { useBrowserUiStore } from "./browser-ui-store";

describe("browser-ui-store", () => {
  beforeEach(() => {
    useBrowserUiStore.setState({ sidebarCollapsed: false });
  });

  it("initial state has sidebarCollapsed=false", () => {
    expect(useBrowserUiStore.getState().sidebarCollapsed).toBe(false);
  });

  it("toggleSidebar flips sidebarCollapsed from false to true", () => {
    useBrowserUiStore.getState().toggleSidebar();
    expect(useBrowserUiStore.getState().sidebarCollapsed).toBe(true);
  });

  it("toggleSidebar flips sidebarCollapsed from true back to false", () => {
    useBrowserUiStore.getState().toggleSidebar();
    expect(useBrowserUiStore.getState().sidebarCollapsed).toBe(true);
    useBrowserUiStore.getState().toggleSidebar();
    expect(useBrowserUiStore.getState().sidebarCollapsed).toBe(false);
  });

  it("setSidebarCollapsed sets the value directly", () => {
    useBrowserUiStore.getState().setSidebarCollapsed(true);
    expect(useBrowserUiStore.getState().sidebarCollapsed).toBe(true);
    useBrowserUiStore.getState().setSidebarCollapsed(false);
    expect(useBrowserUiStore.getState().sidebarCollapsed).toBe(false);
  });

  it("setSidebarCollapsed(true) followed by setSidebarCollapsed(true) is idempotent", () => {
    useBrowserUiStore.getState().setSidebarCollapsed(true);
    useBrowserUiStore.getState().setSidebarCollapsed(true);
    expect(useBrowserUiStore.getState().sidebarCollapsed).toBe(true);
  });
});
