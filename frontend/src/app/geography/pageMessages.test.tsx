// The Geography page loads only the `geo` namespace beyond the shell (GEO-7). These renders use
// the real next-intl with exactly `routeMessages` of the route, so a sentence its client code
// points somewhere else — a `home.*` key the route no longer carries — resolves to its key path
// and fails the assertions. The age line is the part that moved; the body is the part the
// Geography widget shares with the Home dashboard, whose own message set must still carry it.

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The suite's default mock backs `useTranslations` with the whole English catalog; this file
// must measure what the route really carries, so the provider and hooks are the real ones.
vi.unmock("next-intl");

import { NextIntlClientProvider } from "next-intl";

import en from "../../../messages/en";
import { GeographyBody } from "@/components/geo/GeographyBody";
import { answer } from "@/components/geo/fixtures";
import { routeMessages } from "@/i18n/routeNamespaces";
import { geoApi } from "@/lib/api";

import GeographyPage from "./page";

vi.mock("echarts/core", () => ({
  init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), on: vi.fn() }),
  use: vi.fn(),
  registerMap: vi.fn(),
}));
vi.mock("echarts/charts", () => ({ MapChart: {} }));
vi.mock("echarts/components", () => ({ TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
// No cadence read: the 2 h default applies, so an answer 1 h old is fresh.
vi.mock("@/hooks/useApiResource", () => ({ useApiResource: () => ({ status: "loading" }) }));

const NOW = new Date("2026-11-02T12:00:00Z");
const at = (hoursAgo: number) => new Date(NOW.getTime() - hoursAgo * 3_600_000).toISOString();

beforeEach(() => {
  vi.restoreAllMocks();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

/** A namespace-qualified key path, which next-intl renders when a message is missing. */
const KEY_PATH = /\b(?:geo|home|states|units|format|shell|common|errors)\.[a-zA-Z]/;

describe("the Geography page under its own message set", () => {
  it("renders every sentence, the age line included, with only the route's namespaces", async () => {
    vi.spyOn(geoApi, "countries").mockResolvedValue(answer({ latest_bucket_end: at(1) }));
    const page = await GeographyPage({ searchParams: Promise.resolve({}) });
    render(
      <NextIntlClientProvider
        locale="en"
        messages={routeMessages("/geography", en)}
        timeZone="UTC"
      >
        {page}
      </NextIntlClientProvider>,
    );
    // The age line reads `geo.freshness` since GEO-7; a sentence left on `home.frame` would
    // render its key path here and fail this.
    expect(screen.getByText(/^Data to/)).toHaveTextContent("Data to 11:00:00 UTC, 1 h ago.");
    expect(screen.getByRole("table", { name: "Events by country" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(KEY_PATH);
  });

  it("still renders the body the Geography widget shares, under the dashboard's message set", () => {
    render(
      <NextIntlClientProvider locale="en" messages={routeMessages("/", en)} timeZone="UTC">
        <GeographyBody data={answer()} locale="en" limit={5} />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("table", { name: "Events by country" })).toBeInTheDocument();
    expect(screen.getByText(/Top 5 of 18 countries/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(KEY_PATH);
  });
});
