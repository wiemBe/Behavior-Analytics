import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ENABLED_LOCALES, type Locale } from "@/i18n/locale";
import { FROZEN_TR_KEYS } from "@/i18n/trFrozen";
import { GUIDE_SECTIONS } from "@/lib/guideSections";
import tr from "../../../messages/tr";
import { enableTurkish, setTestLocale } from "../../../vitest.setup";
import GuidePage from "./page";
import GuideSectionPage from "./[section]/page";
import GuideSectionNotFound from "./[section]/not-found";

// The table the index page reads, as an array this file may narrow for one test (below). Left
// whole, it is the real table.
const table = vi.hoisted(() => ({ full: [] as unknown[] }));
vi.mock("@/lib/guideSections", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/guideSections")>();
  table.full = [...actual.GUIDE_SECTIONS];
  return { ...actual, GUIDE_SECTIONS: [...actual.GUIDE_SECTIONS] };
});
afterEach(() => (GUIDE_SECTIONS as unknown[]).splice(0, GUIDE_SECTIONS.length, ...table.full));

// D-41: Turkish is frozen at the checkpoint, so a guide section added to English afterwards has no
// Turkish text. The Turkish pages are rendered for the sections the frozen catalog holds, and for
// every section again once `tr` is enabled (the same rule as `lib/guide.contract.test.ts`).
const FROZEN_SECTIONS = new Set(
  FROZEN_TR_KEYS.filter((key) => key.startsWith("guide.sections.")).map((key) => key.split(".")[2]),
);

/** The section ids a locale's pages must render. */
function renderedIn(
  locale: Locale,
  ids: readonly string[] = GUIDE_SECTIONS.map((s) => s.id),
  frozen: ReadonlySet<string> = FROZEN_SECTIONS,
  enabled: readonly Locale[] = ENABLED_LOCALES,
): string[] {
  return ids.filter((id) => locale === "en" || enabled.includes("tr") || frozen.has(id));
}

/** Narrow the table the index page reads to the sections Turkish must render. */
function narrowTableToTurkish() {
  const keep = new Set(renderedIn("tr"));
  (GUIDE_SECTIONS as unknown[]).splice(0, GUIDE_SECTIONS.length, ...table.full.filter((s) => keep.has((s as { id: string }).id)));
}

async function section(id: string) {
  return render(await GuideSectionPage({ params: Promise.resolve({ section: id }) }));
}

describe("guide index", () => {
  it("lists every group, and every section with its title and the question it answers", async () => {
    render(await GuidePage());
    expect(screen.getByRole("heading", { level: 1, name: "Guide" })).toBeInTheDocument();
    const groups = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(groups).toEqual(["Concepts", "Analyst pages", "Analyst tasks", "Administrator tasks"]);
    expect(screen.getAllByRole("listitem")).toHaveLength(GUIDE_SECTIONS.length);
    const entry = screen.getByRole("link", { name: "Set up a WAF source" });
    expect(entry).toHaveAttribute("href", "/guide/waf-source-setup");
    expect(entry.closest("li")).toHaveTextContent("start collecting WAF behavior");
  });

  it("is Turkish with Turkish group names and sections", async () => {
    setTestLocale("tr");
    narrowTableToTurkish();
    render(await GuidePage());
    expect(screen.getByRole("heading", { level: 1, name: "Kılavuz" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Kavramlar", "Analist sayfaları", "Analist görevleri", "Yönetici görevleri",
    ]);
    expect(screen.getByRole("link", { name: tr.guide.sections["honesty-states"].title })).toBeInTheDocument();
  });
});

describe("guide section", () => {
  it("shows a task with its facts, numbered steps, states and related sections", async () => {
    await section("waf-source-setup");
    expect(screen.getByRole("heading", { level: 1, name: "Set up a WAF source" })).toBeInTheDocument();
    const facts = screen.getByText("Permission").closest("dl")!;
    expect(within(facts).getByText("admin:waf").tagName).toBe("CODE");
    expect(within(facts).getByText("Administrators")).toBeInTheDocument();
    const link = within(facts).getByRole("link", { name: "/admin/waf-sources" });
    expect(link).toHaveAttribute("href", "/admin/waf-sources");
    // A route with a parameter is named, not linked.
    expect(within(facts).getByText("/admin/waf-sources/[logSourceId]").closest("a")).toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "Steps" })).toBeInTheDocument();
    expect(document.querySelectorAll("ol.guide-steps > li").length).toBeGreaterThan(5);
    expect(document.querySelector("ol.guide-steps strong")).not.toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "States you may see" })).toBeInTheDocument();
    const related = screen.getByRole("heading", { level: 2, name: "Related" }).nextElementSibling!;
    expect(within(related as HTMLElement).getByRole("link", { name: "WAF behavior" })).toHaveAttribute(
      "href",
      "/guide/waf-behavior",
    );
    expect(screen.getByRole("link", { name: "All topics" })).toHaveAttribute("href", "/guide");
  });

  it("shows a concept without steps, route or states", async () => {
    await section("how-it-works");
    expect(screen.getByRole("heading", { level: 1, name: "How it works" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Steps" })).toBeNull();
    expect(screen.queryByRole("heading", { level: 2, name: "States you may see" })).toBeNull();
    expect(screen.queryByText("Route")).toBeNull();
    expect(screen.getByText("No permission needed")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Related" })).toBeInTheDocument();
  });

  it("uses the section's own permission wording where the catalog has one", async () => {
    await section("search-cadence");
    const facts = screen.getByText("Permission").closest("dl")!;
    expect(facts).toHaveTextContent("(to view)");
    expect(within(facts).getByText("read:search_cadence").tagName).toBe("CODE");
  });

  it("renders in Turkish, with Turkish headings and permission words", async () => {
    setTestLocale("tr");
    await section("search-cadence");
    expect(screen.getByRole("heading", { level: 2, name: "Adımlar" })).toBeInTheDocument();
    expect(screen.getByText("İzin").closest("dl")).toHaveTextContent("görüntülemek için");
    expect(screen.getByRole("heading", { level: 2, name: "İlgili" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tüm konular" })).toBeInTheDocument();
  });

  it("answers an unknown id, or an inherited name, with a 404", async () => {
    for (const id of ["no-such-section", "constructor", "__proto__", ""]) {
      // `notFound()` throws an error whose digest says 404; any other throw is a different failure.
      await expect(GuideSectionPage({ params: Promise.resolve({ section: id }) })).rejects.toMatchObject({
        digest: expect.stringMatching(/NOT_FOUND|;404/),
      });
    }
  });

  it("renders every section of both languages without throwing", async () => {
    for (const locale of ["en", "tr"] as const) {
      setTestLocale(locale);
      for (const id of renderedIn(locale)) {
        const { unmount } = await section(id);
        expect(document.querySelectorAll("h1")).toHaveLength(1);
        unmount();
      }
    }
  });
});

describe("the frozen-Turkish rule for the guide pages (D-41)", () => {
  const ids = ["frozen-one", "added-later"];
  const frozen = new Set(["frozen-one"]);

  it("renders a section added after the checkpoint in English only", () => {
    expect(renderedIn("en", ids, frozen, ["en"])).toEqual(ids);
    expect(renderedIn("tr", ids, frozen, ["en"])).toEqual(["frozen-one"]);
  });

  it("renders every section in Turkish again once `tr` is enabled", () => {
    expect(renderedIn("tr", ids, frozen, ["en", "tr"])).toEqual(ids);
  });

  it("finds post-checkpoint sections in the real table, and none of them in the frozen set", () => {
    const added = GUIDE_SECTIONS.map((s) => s.id).filter((id) => !FROZEN_SECTIONS.has(id));
    expect(added).toContain("siem-load");
    expect(FROZEN_SECTIONS.size).toBeGreaterThan(20);
    expect(renderedIn("tr")).not.toContain("siem-load");
  });

  it("has no Turkish text for a post-checkpoint section: its page fails once Turkish is enabled", async () => {
    setTestLocale("tr");
    enableTurkish();
    expect(renderedIn("tr")).toContain("siem-load");
    await expect(section("siem-load")).rejects.toThrow("guide: no section siem-load");
    await expect(GuidePage()).rejects.toThrow("guide: no section siem-load");
  });

  it("renders every frozen section in Turkish (a deleted one would throw)", async () => {
    setTestLocale("tr");
    expect(renderedIn("tr").length).toBe(FROZEN_SECTIONS.size);
    for (const id of renderedIn("tr")) {
      const { unmount } = await section(id);
      unmount();
    }
  });
});

describe("guide 404 notice", () => {
  it("says the topic was not found and returns to the contents, in both languages", async () => {
    render(await GuideSectionNotFound());
    expect(screen.getByRole("heading", { level: 1, name: "Topic not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All topics" })).toHaveAttribute("href", "/guide");
  });

  it("is Turkish", async () => {
    setTestLocale("tr");
    render(await GuideSectionNotFound());
    expect(screen.getByRole("heading", { level: 1, name: "Konu bulunamadı" })).toBeInTheDocument();
  });
});
