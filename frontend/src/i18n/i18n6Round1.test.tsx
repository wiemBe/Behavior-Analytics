import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { createTranslator } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import tr from "../../messages/tr";
import { setTestLocale } from "../../vitest.setup";
import { Pagination } from "@/components/Pagination";
import { AppNav } from "@/components/shell/AppNav";
import { templateDescription, widgetDescription } from "@/widgets/copy";
import { widgetDef } from "@/widgets/registry";
import { LOG_SOURCE_MONITORING } from "@/widgets/templates";

vi.mock("next/navigation", () => ({ usePathname: () => "/fleet/catchall" }));

beforeEach(() => setTestLocale("tr"));

describe("I18N-6 round 1 shared Turkish copy", () => {
  it("uses gönderen for the catch-all destination and widget copy", () => {
    render(<AppNav />);
    expect(screen.getByRole("link", { name: "Catch-all gönderenleri" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    const t = createTranslator({ locale: "tr", messages: tr, namespace: "widgets" });
    expect(widgetDescription(t as never, widgetDef("table.catchall_senders")!)).toContain(
      "en yeni gönderenler",
    );
    expect(templateDescription(t as never, LOG_SOURCE_MONITORING)).toContain(
      "catch-all gönderenleri",
    );
  });

  it("groups every pagination figure in Turkish and English", () => {
    const view = render(
      <Pagination total={1204} limit={1200} offset={0} basePath="/coverage" />,
    );
    expect(screen.getByRole("navigation", { name: "Sayfalama" })).toHaveTextContent(
      "1–1.200 / 1.204",
    );
    view.unmount();

    setTestLocale("en");
    render(<Pagination total={1204} limit={1200} offset={0} basePath="/coverage" />);
    expect(screen.getByRole("navigation", { name: "Pagination" })).toHaveTextContent(
      "1–1,200 of 1,204",
    );
  });

  it("loads the stylesheet that owns the Source EPS legend presentation", () => {
    const root = resolve(__dirname, "../..");
    const component = readFileSync(
      resolve(root, "src/components/catalog/SourceEps.tsx"),
      "utf8",
    );
    const css = readFileSync(resolve(root, "src/components/home/charts.css"), "utf8");
    expect(component).toContain('import "@/components/home/charts.css";');
    expect(css).toMatch(/\.hc-key-list\s*\{[^}]*list-style:\s*none;/s);
  });
});
