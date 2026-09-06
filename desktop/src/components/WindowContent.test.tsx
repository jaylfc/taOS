import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { useEffect } from "react";
import { WindowContent } from "./WindowContent";

const mountSpy = vi.fn();
const unmountSpy = vi.fn();

vi.mock("@/registry/app-registry", () => {
  function FakeBrowser({
    windowId,
    initialUrl,
  }: {
    windowId: string;
    initialUrl?: string;
  }) {
    const url = initialUrl ?? "";
    useEffect(() => {
      mountSpy(url);
      return () => unmountSpy(url);
    }, [url]);
    return (
      <div data-testid="fake-browser" data-window={windowId}>
        {initialUrl ?? "no-url"}
      </div>
    );
  }
  const stableApp = {
    id: "browser",
    name: "Browser",
    component: () => Promise.resolve({ default: FakeBrowser }),
  };
  return { getApp: () => stableApp };
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("WindowContent", () => {
  it("forwards window props to the lazy app", async () => {
    render(
      <WindowContent
        appId="browser"
        windowId="w1"
        props={{ initialUrl: "https://openclaw.local/ui" }}
        launchNonce={0}
      />,
    );
    await flush();
    expect(screen.getByTestId("fake-browser").textContent).toBe(
      "https://openclaw.local/ui",
    );
  });

  it("remounts the app when launchNonce changes", async () => {
    mountSpy.mockClear();
    unmountSpy.mockClear();

    const { rerender } = render(
      <WindowContent
        appId="browser"
        windowId="w2"
        props={{ initialUrl: "https://a.test" }}
        launchNonce={0}
      />,
    );
    await flush();
    expect(mountSpy).toHaveBeenCalledWith("https://a.test");
    const initialMounts = mountSpy.mock.calls.length;

    rerender(
      <WindowContent
        appId="browser"
        windowId="w2"
        props={{ initialUrl: "https://b.test" }}
        launchNonce={1}
      />,
    );
    await flush();

    expect(unmountSpy).toHaveBeenCalledWith("https://a.test");
    expect(mountSpy).toHaveBeenCalledWith("https://b.test");
    expect(mountSpy.mock.calls.length).toBeGreaterThan(initialMounts);
  });

  it("does NOT remount when launchNonce is unchanged", async () => {
    mountSpy.mockClear();
    unmountSpy.mockClear();

    const { rerender } = render(
      <WindowContent
        appId="browser"
        windowId="w3"
        props={{ initialUrl: "https://a.test" }}
        launchNonce={0}
      />,
    );
    await flush();
    const mountsAfterFirstRender = mountSpy.mock.calls.length;

    rerender(
      <WindowContent
        appId="browser"
        windowId="w3"
        props={{ initialUrl: "https://a.test" }}
        launchNonce={0}
      />,
    );
    await flush();

    expect(mountSpy.mock.calls.length).toBe(mountsAfterFirstRender);
    expect(unmountSpy).not.toHaveBeenCalled();
  });
});
