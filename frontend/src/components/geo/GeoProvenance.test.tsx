// GEO-6: the line under the map says how the plotted buckets were collected, from the buckets
// (`collected_with`) and not from the current setting. An unmeasured range says nothing, and an
// older backend with no `collected_with` keeps the old line (see GeographyBody.test.tsx).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { GeoCollectedWith, GeoCountries } from "@/lib/api";

import { answer, insufficient } from "./fixtures";
import { GeographyBody } from "./GeographyBody";

const PROP = "Source Geographic Location";

const prop = (buckets: number, name: string | null = PROP) => ({
  country_source: "PROPERTY" as const,
  country_property: name,
  buckets,
});
const lookup = (buckets: number) => ({
  country_source: "LOOKUP" as const,
  country_property: null,
  buckets,
});

/** An INSUFFICIENT_DATA answer: no map to draw, so the facts and the line are all there is. */
function view(collected: GeoCollectedWith | null, over: Partial<GeoCountries> = {}) {
  render(
    <GeographyBody
      data={{ ...insufficient(), collected_with: collected, ...over }}
      locale="en"
      limit={null}
    />,
  );
}

const noteTexts = () =>
  Array.from(document.querySelectorAll(".tw-note")).map((node) => node.textContent);

describe("one way in the range", () => {
  it("names the property it was collected from", () => {
    view(
      { ways: [prop(28)], unknown_buckets: 0 },
      { country_source: "PROPERTY", country_property: PROP },
    );
    expect(noteTexts()).toEqual([`Collected from the QRadar property “${PROP}”.`]);
  });

  it("says GEO::LOOKUP when the buckets came from the lookup", () => {
    view({ ways: [lookup(28)], unknown_buckets: 0 }, { country_source: "LOOKUP" });
    expect(noteTexts()).toEqual(["Collected with QRadar's GEO::LOOKUP."]);
  });

  it("describes the buckets and not the setting: lookup buckets under a property setting", () => {
    // The OPS-30 case: the console is set to a property, the buckets were stored by the lookup.
    view(
      { ways: [lookup(28)], unknown_buckets: 0 },
      { country_source: "PROPERTY", country_property: PROP },
    );
    const notes = noteTexts();
    expect(notes[0]).toBe("Collected with QRadar's GEO::LOOKUP.");
    expect(notes).not.toContain(`Collected from the QRadar property “${PROP}”.`);
    expect(notes[1]).toBe(`The current setting is the QRadar property “${PROP}”.`);
  });

  it("adds no line about the setting when it is what the range shows", () => {
    view(
      { ways: [prop(28)], unknown_buckets: 0 },
      { country_source: "PROPERTY", country_property: PROP },
    );
    expect(screen.queryByText(/current setting/)).toBeNull();
  });

  it("treats another property name as a different setting", () => {
    view(
      { ways: [prop(28, "Old Name")], unknown_buckets: 0 },
      { country_source: "PROPERTY", country_property: PROP },
    );
    expect(noteTexts()).toEqual([
      "Collected from the QRadar property “Old Name”.",
      `The current setting is the QRadar property “${PROP}”.`,
    ]);
  });

  it("says the lookup is the current setting when buckets came from a property", () => {
    view({ ways: [prop(28)], unknown_buckets: 0 }, { country_source: "LOOKUP" });
    expect(screen.getByText("The current setting is QRadar's GEO::LOOKUP.")).toBeInTheDocument();
  });

  it("does not invent a name for a property bucket that has none", () => {
    view(
      { ways: [prop(5, null)], unknown_buckets: 0 },
      { country_source: "PROPERTY", country_property: null },
    );
    expect(screen.getByText("Collected from a QRadar property.")).toBeInTheDocument();
  });
});

describe("several ways in the range", () => {
  it("names each way with its bucket count", () => {
    view(
      { ways: [prop(200), lookup(28)], unknown_buckets: 0 },
      { country_source: "PROPERTY", country_property: PROP },
    );
    expect(noteTexts()).toEqual([
      `Collected two ways in this range: the QRadar property “${PROP}” (200 buckets); QRadar's GEO::LOOKUP (28 buckets).`,
      `The current setting is the QRadar property “${PROP}”.`,
    ]);
  });

  it("counts a way of one bucket in the singular and three ways as a number", () => {
    view(
      { ways: [prop(200), prop(5, "Old"), lookup(1)], unknown_buckets: 0 },
      { country_source: "LOOKUP" },
    );
    expect(
      screen.getByText(
        `Collected 3 ways in this range: the QRadar property “${PROP}” (200 buckets); the QRadar property “Old” (5 buckets); QRadar's GEO::LOOKUP (1 bucket).`,
      ),
    ).toBeInTheDocument();
  });
});

describe("buckets that do not record how they were collected", () => {
  it("counts them, in the warning colour, beside the known way", () => {
    view(
      { ways: [prop(200)], unknown_buckets: 28 },
      { country_source: "PROPERTY", country_property: PROP },
    );
    expect(noteTexts()).toEqual([
      `Collected from the QRadar property “${PROP}” (200 buckets).`,
      "28 buckets were collected before 0.1.11 and do not record how.",
      `The current setting is the QRadar property “${PROP}”.`,
    ]);
    expect(screen.getByText(/28 buckets were collected before/)).toHaveClass("warn");
  });

  it("says them alone when no bucket knows, and never names a source", () => {
    view(
      { ways: [], unknown_buckets: 286 },
      { country_source: "PROPERTY", country_property: PROP },
    );
    expect(noteTexts()).toEqual([
      "286 buckets were collected before 0.1.11 and do not record how.",
      `The current setting is the QRadar property “${PROP}”.`,
    ]);
    expect(screen.queryByText(/^Collected from/)).toBeNull();
  });

  it("agrees in number for one bucket", () => {
    view({ ways: [], unknown_buckets: 1 }, { country_source: "LOOKUP" });
    expect(
      screen.getByText("1 bucket was collected before 0.1.11 and does not record how."),
    ).toBeInTheDocument();
  });

  it("says nothing about unknown buckets when there are none", () => {
    view({ ways: [lookup(3)], unknown_buckets: 0 }, { country_source: "LOOKUP" });
    expect(screen.queryByText(/0\.1\.11/)).toBeNull();
  });
});

describe("a destination with no property configured", () => {
  const dest = (collected: GeoCollectedWith) =>
    view(collected, {
      direction: "DESTINATION",
      country_source: "PROPERTY",
      country_property: null,
    });

  it("names the .env key that sets it, never 'From a QRadar property'", () => {
    dest({ ways: [lookup(28)], unknown_buckets: 0 });
    expect(noteTexts()).toEqual([
      "Collected with QRadar's GEO::LOOKUP.",
      "The current setting names no QRadar property for destination addresses. GEO_DESTINATION_COUNTRY_PROPERTY sets it.",
    ]);
    expect(screen.queryByText("From a QRadar property")).toBeNull();
  });

  it("names the source key for the source direction", () => {
    view(
      { ways: [lookup(28)], unknown_buckets: 0 },
      { direction: "SOURCE", country_source: "PROPERTY", country_property: null },
    );
    expect(screen.getByText(/GEO_SOURCE_COUNTRY_PROPERTY sets it\./)).toBeInTheDocument();
  });

  it("names it when only unknown buckets exist", () => {
    dest({ ways: [], unknown_buckets: 4 });
    expect(screen.getByText(/GEO_DESTINATION_COUNTRY_PROPERTY sets it\./)).toBeInTheDocument();
  });
});

describe("no claim without the buckets", () => {
  it("says nothing for an empty answer (collected_with null)", () => {
    view(null, { country_source: "PROPERTY", country_property: PROP });
    expect(screen.queryByText(/Collected|current setting|From the QRadar/)).toBeNull();
  });

  it("says nothing when the answer holds no way and no unknown bucket and no setting", () => {
    view(
      { ways: [], unknown_buckets: 0 },
      { country_source: undefined, country_property: undefined },
    );
    expect(noteTexts()).toEqual([]);
  });

  it("keeps the old line for an older backend that sends no collected_with", () => {
    render(
      <GeographyBody
        data={answer({
          ...insufficient(),
          country_source: "PROPERTY",
          country_property: PROP,
        })}
        locale="en"
        limit={null}
      />,
    );
    expect(screen.getByText(`From the QRadar property “${PROP}”`)).toBeInTheDocument();
  });
});
