import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Blocks, Markup } from "./Markup";

function html(text: string): string {
  return render(<Markup text={text} />).container.innerHTML;
}

describe("guide markup", () => {
  it("turns **label** into strong and `code` into code", () => {
    expect(html("Choose **Save** and read `admin:waf`.")).toBe(
      "Choose <strong>Save</strong> and read <code>admin:waf</code>.",
    );
  });

  it("allows code inside a bold span and keeps the order of several spans", () => {
    expect(html("**Off `x`** then `y` then **On**")).toBe(
      "<strong>Off <code>x</code></strong> then <code>y</code> then <strong>On</strong>",
    );
  });

  it("leaves an unclosed mark as plain text", () => {
    expect(html("a ** b and a ` c")).toBe("a ** b and a ` c");
    expect(html("**open")).toBe("**open");
  });

  it("renders a tag in a value as text, never as an element", () => {
    const { container } = render(<Markup text={'Use **<b onclick="x()">bold</b>** and <script>alert(1)</script>'} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toBe('Use <b onclick="x()">bold</b> and <script>alert(1)</script>');
    expect(container.querySelector("strong")?.textContent).toBe('<b onclick="x()">bold</b>');
  });

  it("keeps a placeholder as written", () => {
    expect(html("**Save roles for {name}**")).toBe("<strong>Save roles for {name}</strong>");
  });

  it("renders paragraphs, bullet lists and sub-headings", () => {
    const { container } = render(
      <Blocks blocks={[{ p: "one" }, { h: "How to read it" }, { ul: ["a", "**b**"] }]} />,
    );
    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(container.querySelector("h3")?.textContent).toBe("How to read it");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("li strong")?.textContent).toBe("b");
  });
});
