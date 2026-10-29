import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GUIDE_SECTIONS } from "@/lib/guideSections";
import tr from "../../../messages/tr";
import { setTestLocale } from "../../../vitest.setup";
import GuidePage from "./page";
import GuideSectionPage from "./[section]/page";
import GuideSectionNotFound from "./[section]/not-found";

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
      for (const { id } of GUIDE_SECTIONS) {
        const { unmount } = await section(id);
        expect(document.querySelectorAll("h1")).toHaveLength(1);
        unmount();
      }
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
