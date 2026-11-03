import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HostName } from "./HostName";

describe("HostName", () => {
  it("keeps a name with a hyphen and no dot as one run", () => {
    const { container } = render(<HostName host="qr-console" />);
    expect(container.querySelector(".sl-host")).toHaveClass("sl-label");
    expect(container.querySelector(".sl-host")?.children).toHaveLength(0);
    expect(container.querySelector("wbr")).toBeNull();
    expect(container).toHaveTextContent("qr-console");
  });

  it("offers a break only after each dot", () => {
    const { container } = render(<HostName host="a.b.c" />);
    expect([...container.querySelectorAll(".sl-label")].map((el) => el.textContent)).toEqual([
      "a.",
      "b.",
      "c",
    ]);
    expect(container.querySelectorAll("wbr")).toHaveLength(2);
  });
});
