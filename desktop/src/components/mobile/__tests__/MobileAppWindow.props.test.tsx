import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { useEffect } from "react";
import { MobileAppWindow } from "../MobileAppWindow";

const mountSpy = vi.fn();

vi.mock("@/registry/app-registry", () => {
  function FakeBrowser({ windowId, initialUrl }: { windowId: string; initialUrl?: string }) {
    const url = initialUrl ?? "";
    useEffect(() => { mountSpy(url); }, [url]);
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

const noop = () => {};

describe("MobileAppWindow", () => {
  // The phone layout renders apps here, not through WindowContent: a window
  // opened with props (a first chat, a launch URL) must get them on a phone too.
  it("forwards window props to the lazy app", async () => {
    render(
      <MobileAppWindow appId="browser" windowId="w1" props={{ initialUrl: "https://openclaw.local/ui" }}
        launchNonce={0} onClose={noop} onMinimise={noop} />,
    );
    await flush();
    expect(screen.getByTestId("fake-browser").textContent).toBe("https://openclaw.local/ui");
  });

  it("remounts the app with the new props when the window is launched again", async () => {
    mountSpy.mockClear();
    const { rerender } = render(
      <MobileAppWindow appId="browser" windowId="w1" props={{ initialUrl: "a" }} launchNonce={0}
        onClose={noop} onMinimise={noop} />,
    );
    await flush();
    rerender(
      <MobileAppWindow appId="browser" windowId="w1" props={{ initialUrl: "b" }} launchNonce={1}
        onClose={noop} onMinimise={noop} />,
    );
    await flush();
    expect(screen.getByTestId("fake-browser").textContent).toBe("b");
    expect(mountSpy.mock.calls.map((c) => c[0])).toEqual(["a", "b"]);
  });
});
