import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { PropertyInput } from "./PropertyInput";
import { catalog } from "./fixtures";
import type { CatalogProperty } from "@/lib/api";
import { matchProperty, searchTokens } from "@/lib/fieldMapping";

const PROPS = catalog().properties;

function Harness({
  initial = "",
  suggestions = PROPS,
  normalized,
  onChange,
}: {
  initial?: string;
  suggestions?: CatalogProperty[];
  normalized?: string[];
  onChange?: (v: string) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <PropertyInput
        id="p"
        label="Probe property"
        value={value}
        suggestions={suggestions}
        normalized={normalized}
        onChange={(v) => {
          setValue(v);
          onChange?.(v);
        }}
      />
      <button type="button">after</button>
    </>
  );
}

const box = () => screen.getByRole("combobox", { name: "Probe property" });
const names = () =>
  screen.queryAllByRole("option").map((o) => o.querySelector(".fm-option-name")?.textContent);
const many = (n: number): CatalogProperty[] =>
  Array.from({ length: n }, (_, i) => ({
    name: `Prop_${i}_Field`,
    property_type: "STRING",
    applies_to_source_type: i % 3 === 0 ? true : i % 3 === 1 ? false : null,
    name_allowed: true,
  }));

describe("matchProperty", () => {
  it("splits tokens on spaces, underscores, hyphens and camelCase", () => {
    expect(searchTokens("resp code")).toEqual(["resp", "code"]);
    expect(searchTokens("WAF_ResponseCode")).toEqual(["waf", "response", "code"]);
    expect(searchTokens("HTTPMethod-x")).toEqual(["http", "method", "x"]);
  });

  it.each(["resp code", "responsecode", "waf resp", "WAFResponse", "code", "CODE resp"])(
    "finds WAF_ResponseCode with %s",
    (q) => {
      expect(matchProperty("WAF_ResponseCode", q)).not.toBeNull();
    },
  );

  it("does not match a token that is absent", () => {
    expect(matchProperty("WAF_ResponseCode", "resp url")).toBeNull();
  });

  it("returns the ranges of the matched characters", () => {
    expect(matchProperty("WAF_ResponseCode", "resp code")?.ranges).toEqual([[4, 8], [12, 16]]);
    expect(matchProperty("WAF_ResponseCode", "wafresp")?.ranges).toEqual([[0, 8]]);
  });

  it("prefers an occurrence at the start of a word", () => {
    expect(matchProperty("Decoded_Code", "code")?.ranges).toEqual([[8, 12]]);
  });

  it("ranks word-start matches ahead of mid-word ones", () => {
    const a = matchProperty("Decoded", "code")!;
    const b = matchProperty("Code", "code")!;
    expect(b.score).toBeLessThan(a.score);
  });
});

describe("PropertyInput", () => {
  it("opens on an empty value with the whole list, grouped, with a count", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(box());
    expect(box()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("status")).toHaveTextContent("6 properties");
    expect(names()).toHaveLength(6);
    const groups = screen.getAllByRole("group");
    expect(groups.map((g) => g.firstElementChild?.textContent)).toEqual([
      "Applies to this source type4",
      "Other source types2",
    ]);
    expect(within(groups[1]).getAllByRole("option")).toHaveLength(2);
  });

  it("opens on a set value with everything listed and the current one marked", async () => {
    const user = userEvent.setup();
    render(<Harness initial="WAF_Action" />);
    await user.click(box());
    expect(names()).toHaveLength(6);
    const marked = screen.getAllByRole("option", { selected: true });
    expect(marked).toHaveLength(1);
    expect(marked[0]).toHaveTextContent("WAF_Action");
    expect(marked[0]).toHaveTextContent("current");
    expect(box()).toHaveAttribute("aria-activedescendant", marked[0].id);
  });

  it("filters by tokens and emphasises the matched characters", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(box());
    await user.type(box(), "resp code");
    expect(names()).toEqual(["WAF_ResponseCode"]);
    expect(screen.getByRole("status")).toHaveTextContent("1 of 6 properties match");
    const marks = Array.from(document.querySelectorAll("mark.fm-hit")).map((m) => m.textContent);
    expect(marks).toEqual(["Resp", "Code"]);
  });

  it("finds by a single run-together word and by a camelCase query", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(box(), "responsecode");
    expect(names()).toEqual(["WAF_ResponseCode"]);
    await user.clear(box());
    await user.type(box(), "httpMethod");
    expect(names()).toEqual(["HTTP_Method"]);
  });

  it("says nothing matches and that the typed name can still be used", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await user.type(box(), "zzz");
    expect(names()).toHaveLength(0);
    expect(screen.getByRole("status")).toHaveTextContent(
      "0 of 6 properties match for “zzz”. The name you typed can still be used; validation will check it.",
    );
    await user.keyboard("{Enter}");
    expect(box()).toHaveValue("zzz");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("keeps the count in a polite live region", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(box());
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });

  it("moves with Down, Up, Home and End, skipping a name that is not selectable", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(box());
    await user.keyboard("{ArrowDown}");
    const active = () => document.getElementById(box().getAttribute("aria-activedescendant")!);
    expect(active()).toHaveTextContent("HTTP_Method");
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}");
    expect(active()).toHaveTextContent("WAF_ResponseCode");
    await user.keyboard("{End}");
    expect(active()).toHaveTextContent("WAF_URL");
    await user.keyboard("{Home}");
    expect(active()).toHaveTextContent("HTTP_Method");
    await user.keyboard("{ArrowUp}");
    expect(active()).toHaveTextContent("HTTP_Method");
    await user.keyboard("{End}{ArrowDown}");
    expect(active()).toHaveTextContent("WAF_URL");
  });

  it("leaves Home and End to the text cursor until an option is active", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(box(), "abc");
    await user.keyboard("{Home}");
    expect(box().getAttribute("aria-activedescendant")).toBeNull();
  });

  it("chooses the active option with Enter and closes", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(box());
    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(box()).toHaveValue("URL");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("opens with Down on a closed picker and starts at the current value", async () => {
    const user = userEvent.setup();
    render(<Harness initial="URL" />);
    box().focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(document.getElementById(box().getAttribute("aria-activedescendant")!)).toHaveTextContent("URL");
  });

  it("restores the previous value on Escape", async () => {
    const user = userEvent.setup();
    render(<Harness initial="URL" />);
    await user.click(box());
    await user.keyboard("resp");
    expect(box()).toHaveValue("resp");
    await user.keyboard("{Escape}");
    expect(box()).toHaveValue("URL");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("keeps free text when Tab leaves the field", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(box(), "My_Own_Property");
    await user.tab();
    expect(box()).toHaveValue("My_Own_Property");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("chooses with the mouse", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(box());
    await user.click(screen.getByRole("option", { name: /WAF_Action/ }));
    expect(box()).toHaveValue("WAF_Action");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("cannot choose a name that is not selectable, by mouse or keyboard", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(box());
    const bad = screen.getByRole("option", { name: /bad;name/ });
    expect(bad).toHaveAttribute("aria-disabled", "true");
    expect(bad).toHaveTextContent("not selectable: name not allowed");
    await user.click(bad);
    expect(box()).toHaveValue("");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await user.keyboard("{End}");
    await user.keyboard("{ArrowDown}");
    expect(document.getElementById(box().getAttribute("aria-activedescendant")!)).not.toBe(bad);
  });

  it("is a plain text input when there is nothing to list", async () => {
    const user = userEvent.setup();
    render(<Harness suggestions={[]} />);
    await user.click(box());
    await user.type(box(), "Anything");
    await user.keyboard("{ArrowDown}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(box()).toHaveAttribute("aria-expanded", "false");
    expect(box()).toHaveValue("Anything");
  });

  it("lists normalized options first, under their own header, and searches them", async () => {
    const user = userEvent.setup();
    render(<Harness normalized={["sourceip", "username"]} />);
    await user.click(box());
    const groups = screen.getAllByRole("group");
    expect(groups[0].firstElementChild).toHaveTextContent("QRadar normalized fields");
    expect(names().slice(0, 2)).toEqual(["sourceip", "username"]);
    await user.type(box(), "user");
    expect(names()).toEqual(["username"]);
  });

  it("puts a null type under Type unknown", async () => {
    const user = userEvent.setup();
    render(<Harness suggestions={many(6)} />);
    await user.click(box());
    expect(screen.getAllByRole("group").map((g) => g.firstElementChild?.textContent)).toEqual([
      "Applies to this source type2",
      "Other source types2",
      "Type unknown2",
    ]);
  });

  it(
    "renders and filters 500 properties without a cap",
    async () => {
      // The 500 options re-render on every keystroke, so typing the whole query
      // character by character (315-374 ms of this test) is work it is not about;
      // pasting the same query takes 12-14 ms. PropertyInput filters in the
      // input's onChange, which a paste fires exactly as a typed character does,
      // so the filtering path and every assertion below are unchanged. delay: null
      // drops user-event's per-event timer waits, which are wall-clock too.
      const user = userEvent.setup({ delay: null });
      render(<Harness suggestions={many(500)} />);
      await user.click(box());
      expect(names()).toHaveLength(500);
      expect(screen.getByRole("status")).toHaveTextContent("500 properties");
      await user.paste("prop 499 field");
      expect(names()).toEqual(["Prop_499_Field"]);
      expect(screen.getByRole("status")).toHaveTextContent("1 of 500 properties match");
    },
    // Wall-clock budget only; no assertion depends on it. Opening and then
    // querying all 500 options costs about 180 ms alone, but this file runs
    // alongside 110 others, where it has measured 3869 ms of Vitest's 5000 ms
    // default and still timed out at 6898 ms under a heavier load. What the test
    // must assert fixes that work, so give it room rather than raising the global
    // testTimeout.
    15_000,
  );
});
