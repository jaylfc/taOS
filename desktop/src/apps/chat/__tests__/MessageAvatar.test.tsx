import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { MessageAvatar } from "../MessageAvatar";

describe("MessageAvatar agent tile", () => {
  it("renders a rounded-[8px] gradient tile with the emoji for variant=tile", () => {
    const { container } = render(
      <MessageAvatar size={28} authorId="tom" displayName="tom" kind="agent" emoji="🤖" variant="tile" />,
    );
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain("rounded-[8px]");
    expect(el.style.backgroundImage).toContain("linear-gradient");
    expect(el.textContent).toBe("🤖");
  });

  it("is deterministic per author id", () => {
    const a = render(<MessageAvatar size={28} authorId="tom" displayName="tom" kind="agent" variant="tile" />);
    const b = render(<MessageAvatar size={28} authorId="tom" displayName="tom" kind="agent" variant="tile" />);
    expect((a.container.firstElementChild as HTMLElement).style.backgroundImage).toBe(
      (b.container.firstElementChild as HTMLElement).style.backgroundImage,
    );
  });

  it("falls back to the initial when no emoji is given", () => {
    const { container } = render(
      <MessageAvatar size={28} authorId="tom" displayName="tom" kind="agent" variant="tile" />,
    );
    expect(container.textContent).toBe("T");
  });

  it("leaves the default agent avatar unchanged", () => {
    const { container } = render(
      <MessageAvatar size={30} authorId="tom" displayName="tom" kind="agent" emoji="🤖" />,
    );
    expect((container.firstElementChild as HTMLElement).className).toContain("rounded-[11px]");
  });
});
