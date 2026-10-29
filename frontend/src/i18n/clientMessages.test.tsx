import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import RootLayout from "@/app/layout";
import en from "../../messages/en";
import tr from "../../messages/tr";
import { setTestLocale } from "../../vitest.setup";
import { clientMessages } from "./clientMessages";

describe("client messages", () => {
  it("drop the guide and keep every other namespace", () => {
    for (const catalog of [en, tr]) {
      const sent = clientMessages(catalog) as Record<string, unknown>;
      expect(Object.hasOwn(sent, "guide")).toBe(false);
      expect(Object.keys(sent).sort()).toEqual(Object.keys(catalog).filter((k) => k !== "guide").sort());
      expect(Object.hasOwn(sent, "guideLinks")).toBe(true);
    }
  });

  it("do not carry a section's text anywhere", () => {
    const sent = JSON.stringify(clientMessages(en));
    expect(sent).not.toContain(en.guide.sections["waf-source-setup"].answers);
  });

  for (const locale of ["en", "tr"] as const) {
    it(`reach the browser through the root layout without the guide (${locale})`, () => {
      setTestLocale(locale);
      // The layout is a plain function over mocked hooks: walk html > body > provider.
      const html = RootLayout({ children: null }) as ReactElement<{ children: ReactElement<{ children: ReactElement }> }>;
      const provider = html.props.children.props.children as ReactElement<{ messages: Record<string, unknown> }>;
      const messages = provider.props.messages;
      expect(Object.hasOwn(messages, "guide")).toBe(false);
      expect(Object.hasOwn(messages, "shell")).toBe(true);
    });
  }
});
