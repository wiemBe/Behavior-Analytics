// The geography view in every state it can be in, in English and in Turkish. ECharts is
// replaced by a recorder, so the option the map was given can be read; the outlines are the real
// lazy chunk.

import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";

import {
  answer,
  countries,
  insufficient,
  MIXED,
  notCollected,
  partialLowerBound,
} from "./fixtures";
import { GeographyBody } from "./GeographyBody";

const { setOption, init, use, registerMap } = vi.hoisted(() => {
  const setOption = vi.fn();
  const dispose = vi.fn();
  return {
    setOption,
    registerMap: vi.fn(),
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose, on: vi.fn() })),
    use: vi.fn(),
  };
});
vi.mock("echarts/core", () => ({ init, use, registerMap }));
vi.mock("echarts/charts", () => ({ MapChart: {} }));
vi.mock("echarts/components", () => ({ TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

beforeEach(() => {
  setOption.mockClear();
  init.mockClear();
  registerMap.mockClear();
});

const rowsOf = () => {
  const table = screen.getByRole("table", { name: /Events by country|Ülkeye göre olaylar/ });
  return within(table).getAllByRole("row").slice(1);
};

describe("an OK answer", () => {
  it("draws the map once the outlines have loaded, as one named image", async () => {
    render(<GeographyBody data={answer()} locale="en" limit={null} />);
    const map = await screen.findByRole("img");
    expect(map).toHaveAccessibleName(
      "Map of events by country, source addresses. Largest: United States (1,240,000), Germany (402,300), China (310,000). The table lists every country.",
    );
    // The image is in the DOM one commit before its effect creates the chart.
    await waitFor(() => expect(init).toHaveBeenCalledTimes(1));
    expect(registerMap).toHaveBeenCalledTimes(1);
    expect(setOption).toHaveBeenCalledTimes(1);
  });

  it("lists every country with its events and share, right-aligned columns named in the header", () => {
    render(<GeographyBody data={answer()} locale="en" limit={null} />);
    const rows = rowsOf();
    expect(rows).toHaveLength(MIXED.length);
    const first = within(rows[0]).getAllByRole("cell");
    expect(first[0]).toHaveTextContent("United States");
    expect(first[0]).toHaveTextContent("US");
    expect(first[1]).toHaveTextContent("1,240,000");
    expect(first[2]).toHaveTextContent(/%/);
    expect(first[1]).toHaveClass("num");
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["Country", "Events", "Share"]);
    expect(screen.getByText("18 countries.")).toBeInTheDocument();
  });

  it("names the legend with its unit, lists the classes, and shows no data apart from them", () => {
    render(<GeographyBody data={answer()} locale="en" limit={null} />);
    expect(screen.getByText("Events per country")).toBeInTheDocument();
    const legend = screen.getByRole("list", { name: "Events per country" });
    const items = within(legend).getAllByRole("listitem");
    // The classes first, the "no data" entry last and with its own fill.
    expect(items.length).toBeGreaterThanOrEqual(3);
    expect(items[items.length - 1]).toHaveTextContent("No data");
    expect(items[items.length - 1].querySelector(".geo-ramp-none")).not.toBeNull();
    for (const item of items.slice(0, -1)) {
      expect(item.querySelector(".geo-ramp-none")).toBeNull();
      expect(item.textContent).toMatch(/\d/);
    }
    expect(items[0].textContent).toMatch(/ to /);
    expect(items[items.length - 2].textContent).toMatch(/or more$/);
  });

  it("limits the table to the rows asked for and says how many there are", () => {
    render(<GeographyBody data={answer()} locale="en" limit={5} />);
    expect(rowsOf()).toHaveLength(5);
    expect(
      screen.getByText("Top 5 of 18 countries. The Geography page lists all of them."),
    ).toBeInTheDocument();
  });

  it("does not claim a top list when the answer fits the limit", () => {
    render(
      <GeographyBody data={answer({ countries: countries(MIXED.slice(0, 3)) })} locale="en" limit={5} />,
    );
    expect(rowsOf()).toHaveLength(3);
    expect(screen.queryByText(/^Top /)).toBeNull();
    expect(screen.getByText("3 countries.")).toBeInTheDocument();
  });

  it("names a country that has no polygon and keeps it in the table", async () => {
    render(<GeographyBody data={answer()} locale="en" limit={null} />);
    expect(
      await screen.findByText(/1 country is too small for the map outlines and not drawn: Singapore\. 95 events are in the table\./),
    ).toBeInTheDocument();
    expect(rowsOf().some((row) => row.textContent?.includes("Singapore"))).toBe(true);
  });

  it("gives the remainders apart from the countries, never as a share", () => {
    render(<GeographyBody data={answer()} locale="en" limit={null} />);
    const facts = document.querySelector(".geo-facts dl") as HTMLElement;
    expect(within(facts).getByText("Unresolved").nextElementSibling).toHaveTextContent("912,400 events");
    expect(within(facts).getByText("Over the country cap").nextElementSibling).toHaveTextContent("3,100 events");
    expect(within(facts).getByText("Unparseable").nextElementSibling).toHaveTextContent("18 events");
    expect(within(facts).getByText("Buckets measured").nextElementSibling).toHaveTextContent("288 of 288");
    const shares = rowsOf().map((r) => within(r).getAllByRole("cell")[2].textContent);
    expect(shares.join(" ")).not.toContain("912");
  });

  it("makes the unresolved note reachable, as the server worded it", () => {
    render(<GeographyBody data={answer()} locale="en" limit={null} />);
    const summary = screen.getByText("What the remainders mean");
    expect(summary.closest("details")).not.toBeNull();
    expect(
      screen.getByText(/UNRESOLVED counts events whose address is private, internal or unknown to GeoIP\./),
    ).toBeInTheDocument();
  });

  it("says nothing about a partial or lower-bound range", () => {
    render(<GeographyBody data={answer()} locale="en" limit={null} />);
    expect(screen.queryByText(/^Partial/)).toBeNull();
    expect(screen.queryByText(/lower bounds/)).toBeNull();
    expect(screen.queryByText(/≥/)).toBeNull();
  });
});

describe("where the country comes from", () => {
  it("names the QRadar property, quoted, when the country comes from one", () => {
    render(
      <GeographyBody
        data={answer({ country_source: "PROPERTY", country_property: "GeoSourceCountry" })}
        locale="en"
        limit={null}
      />,
    );
    expect(screen.getByText("From the QRadar property “GeoSourceCountry”")).toBeInTheDocument();
  });

  it("says a QRadar property without inventing a name when the name is missing", () => {
    render(
      <GeographyBody
        data={answer({ country_source: "PROPERTY", country_property: null })}
        locale="en"
        limit={null}
      />,
    );
    expect(screen.getByText("From a QRadar property")).toBeInTheDocument();
  });

  it("says GeoIP lookup when the country comes from the address", () => {
    render(<GeographyBody data={answer({ country_source: "LOOKUP" })} locale="en" limit={null} />);
    expect(screen.getByText("From the GeoIP lookup of the address")).toBeInTheDocument();
  });

  it.each([undefined, null])("says nothing about it when the source is %s", (source) => {
    const data = answer({ country_source: source });
    delete (data as { country_property?: unknown }).country_property;
    render(<GeographyBody data={data} locale="en" limit={null} />);
    expect(screen.queryByText(/^From /)).toBeNull();
  });

  it("renders a property name that looks like markup as text", () => {
    render(
      <GeographyBody
        data={answer({ country_source: "PROPERTY", country_property: "<b>x</b>" })}
        locale="en"
        limit={null}
      />,
    );
    expect(screen.getByText("From the QRadar property “<b>x</b>”")).toBeInTheDocument();
    expect(document.querySelector(".geo-facts b")).toBeNull();
  });
});

describe("a PARTIAL answer with lower-bound counts", () => {
  it("draws the map and says how much of the range it covers", async () => {
    render(<GeographyBody data={partialLowerBound()} locale="en" limit={null} />);
    expect(await screen.findByRole("img")).toBeInTheDocument();
    expect(
      screen.getByText(/The counts cover only the measured buckets\./),
    ).toBeInTheDocument();
    expect(screen.getByText("Buckets measured").nextElementSibling).toHaveTextContent("211 of 288");
    expect(screen.getByText("Partial")).toBeInTheDocument();
  });

  it("reads at least in the legend, the table and the qualifier", () => {
    render(<GeographyBody data={partialLowerBound()} locale="en" limit={null} />);
    expect(screen.getByText("At least, events per country")).toBeInTheDocument();
    expect(screen.getByText(/every count is at least what is shown/)).toBeInTheDocument();
    const cell = within(rowsOf()[0]).getAllByRole("cell")[1];
    expect(cell).toHaveTextContent("≥ 1,240,000");
    expect(within(cell).getByText(/at least/)).toHaveClass("sr-only");
    const facts = document.querySelector(".geo-facts dl") as HTMLElement;
    expect(within(facts).getByText("Unresolved").nextElementSibling).toHaveTextContent(/^≥ 912,400 events/);
  });

  it("names the property for a property-sourced answer", () => {
    render(<GeographyBody data={partialLowerBound()} locale="en" limit={null} />);
    expect(screen.getByText("From the QRadar property “GeoSourceCountry”")).toBeInTheDocument();
  });
});

describe("a NOT_COLLECTED answer", () => {
  it("draws no map and no table, and says nothing was collected and why that may be", () => {
    render(<GeographyBody data={notCollected()} locale="en" limit={null} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(init).not.toHaveBeenCalled();
    expect(screen.getByText("Not collected.")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Nothing has been collected for source addresses in this range, so no map is drawn\. The QRadar property that carries the country may not be configured/,
      ),
    ).toBeInTheDocument();
  });

  it("names the direction it was asked for", () => {
    render(
      <GeographyBody data={{ ...notCollected(), direction: "DESTINATION" }} locale="en" limit={null} />,
    );
    expect(screen.getByText(/for destination addresses in this range/)).toBeInTheDocument();
  });

  it("shows the coverage and no zero for a remainder that was never counted", () => {
    render(<GeographyBody data={notCollected()} locale="en" limit={null} />);
    expect(screen.getByText("0 of 288")).toBeInTheDocument();
    expect(screen.queryByText("Unresolved")).toBeNull();
    expect(screen.queryByText(/0 events/)).toBeNull();
  });
});

describe("an INSUFFICIENT_DATA answer", () => {
  it("draws no map, says no address resolved, and still lists the remainders", () => {
    render(<GeographyBody data={insufficient()} locale="en" limit={null} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(init).not.toHaveBeenCalled();
    expect(screen.getByText("Insufficient data.")).toBeInTheDocument();
    expect(
      screen.getByText(/but no address resolved to a country, so no map is drawn\./),
    ).toBeInTheDocument();
    expect(screen.getByText("Unresolved").nextElementSibling).toHaveTextContent("40,100 events");
    // Said once: the state is the sentence, not the sentence and a qualifier repeating it.
    expect(screen.getAllByText(/Insufficient data/)).toHaveLength(1);
  });
});

describe("in Turkish", () => {
  beforeEach(() => setTestLocale("tr"));

  it("names the countries, the columns and the legend in Turkish", () => {
    render(<GeographyBody data={answer()} locale="tr" limit={5} />);
    const rows = rowsOf();
    expect(rows[0]).toHaveTextContent("Amerika Birleşik Devletleri");
    expect(rows[1]).toHaveTextContent("Almanya");
    expect(within(rows[0]).getAllByRole("cell")[1]).toHaveTextContent("1.240.000");
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Ülke",
      "Olay",
      "Pay",
    ]);
    expect(screen.getByText("Ülke başına olay")).toBeInTheDocument();
    expect(screen.getByText("Veri yok")).toBeInTheDocument();
    expect(screen.getByText(/^İlk 5 \/ 18 ülke\./)).toBeInTheDocument();
    expect(screen.getByText("Çözümlenemeyen").nextElementSibling).toHaveTextContent("912.400 olay");
  });

  it("words the unresolved note in Turkish, not the server's English", () => {
    render(<GeographyBody data={answer()} locale="tr" limit={5} />);
    expect(screen.getByText(/Çözümlenemeyen, adresi özel, dahili veya GeoIP/)).toBeInTheDocument();
    expect(screen.queryByText(/UNRESOLVED counts/)).toBeNull();
  });

  it("says NOT_COLLECTED, INSUFFICIENT_DATA and PARTIAL in Turkish", () => {
    const { unmount } = render(<GeographyBody data={notCollected()} locale="tr" limit={null} />);
    expect(screen.getByText("Toplanmadı.")).toBeInTheDocument();
    expect(screen.getByText(/yapılandırılmamış olabilir/)).toBeInTheDocument();
    unmount();
    const second = render(<GeographyBody data={insufficient()} locale="tr" limit={null} />);
    expect(screen.getByText("Yetersiz veri.")).toBeInTheDocument();
    second.unmount();
    render(<GeographyBody data={partialLowerBound()} locale="tr" limit={null} />);
    expect(screen.getByText(/Sayımlar yalnızca ölçülen kovaları kapsıyor/)).toBeInTheDocument();
    expect(screen.getByText("Ölçülen kova").nextElementSibling).toHaveTextContent("211 / 288");
    expect(screen.getByText(/En az/, { selector: ".kind" })).toBeInTheDocument();
    expect(screen.getByText("QRadar özelliğinden: “GeoSourceCountry”")).toBeInTheDocument();
  });

  it("names the provenance of a lookup in Turkish", () => {
    render(<GeographyBody data={answer({ country_source: "LOOKUP" })} locale="tr" limit={null} />);
    expect(screen.getByText("Adresin GeoIP aramasından")).toBeInTheDocument();
  });
});
