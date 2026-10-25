// The Home route's own words in Turkish: the document title and the line shown while the page
// reads its query. The dashboard itself is HomeDashboard's.

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeFallback } from "@/components/home/HomeFallback";
import { setTestLocale } from "../../vitest.setup";

import { generateMetadata } from "./page";

vi.mock("@/components/home/HomeDashboard", () => ({ HomeDashboard: () => null }));

beforeEach(() => setTestLocale("tr"));

describe("the Home route in Turkish", () => {
  it("titles the document in Turkish", async () => {
    expect(await generateMetadata()).toEqual({ title: "Gösterge paneli" });
  });

  it("says the dashboard is loading, as polite text", () => {
    render(<HomeFallback />);
    const line = screen.getByRole("status");
    expect(line).toHaveTextContent("Gösterge paneli yükleniyor…");
    expect(line).toHaveAttribute("aria-live", "polite");
  });

  it("titles the document in English by default", async () => {
    setTestLocale("en");
    expect(await generateMetadata()).toEqual({ title: "Dashboard" });
  });
});
