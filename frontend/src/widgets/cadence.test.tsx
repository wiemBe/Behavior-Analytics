import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WidgetFrame } from "@/components/home/WidgetFrame";
import { ApiError, homeReads, type SearchCadenceItem } from "@/lib/api";

import { CadenceProvider, useFamilyTiming } from "./cadence";

const item = (family: SearchCadenceItem["family"], cadence: number, bucket: number | null) =>
  ({
    family,
    cadence_seconds: cadence,
    bucket_seconds: bucket,
  }) as SearchCadenceItem;

function Probe({ family }: { family: SearchCadenceItem["family"] }) {
  const t = useFamilyTiming(family);
  return <p data-testid={family}>{`${t.cadenceSeconds}/${t.bucketSeconds}`}</p>;
}

// Several widgets of one dashboard, each judging its own data time.
function Dashboard({ now }: { now: number }) {
  const at = new Date(now - 1200 * 1000).toISOString();
  return (
    <CadenceProvider>
      {(["PHASE_A_VOLUME", "WAF_COLLECTION", "PHASE_A_VOLUME"] as const).map((family, i) => (
        <WidgetFrame
          key={i}
          id={`w${i}`}
          title={`Widget ${i}`}
          link={{ href: "/x", label: "Open" }}
          what="x"
          permission="read:fleet"
          state={{ kind: "ok", data: "d", asOf: null }}
          dataTo={{ at, family }}
        >
          {() => <p>body {i}</p>}
        </WidgetFrame>
      ))}
    </CadenceProvider>
  );
}

// A note's text is split by its <time> element: match the paragraph's whole text.
const notes = (re: RegExp) =>
  screen.queryAllByText((_c, el) => el?.tagName === "P" && re.test(el.textContent ?? ""));

afterEach(() => vi.restoreAllMocks());

describe("CadenceProvider", () => {
  it("reads the cadence once for a dashboard of several widgets", async () => {
    const fetch = vi.fn(async () => [
      item("PHASE_A_VOLUME", 300, 300),
      item("WAF_COLLECTION", 900, 300),
    ]);
    vi.spyOn(homeReads, "searchCadence").mockReturnValue({
      key: "/admin/search-cadence?test=once",
      fetch,
    });
    render(<Dashboard now={Date.now()} />);
    // 20 min old: stale for Phase A (cadence 5 min), fresh for WAF (15 min).
    await waitFor(() => expect(notes(/^Stale:/)).toHaveLength(2));
    expect(notes(/^Data to/)).toHaveLength(1);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("gives every consumer the family's cadence and bucket width", async () => {
    vi.spyOn(homeReads, "searchCadence").mockReturnValue({
      key: "/admin/search-cadence?test=timing",
      fetch: async () => [item("PHASE_A_VOLUME", 300, 300), item("ANOMALY_EXPLANATION", 900, null)],
    });
    render(
      <CadenceProvider>
        <Probe family="PHASE_A_VOLUME" />
        <Probe family="ANOMALY_EXPLANATION" />
        <Probe family="WAF_COLLECTION" />
      </CadenceProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("PHASE_A_VOLUME")).toHaveTextContent("300/300"));
    expect(screen.getByTestId("ANOMALY_EXPLANATION")).toHaveTextContent("900/null");
    expect(screen.getByTestId("WAF_COLLECTION")).toHaveTextContent("null/null");
  });

  it("leaves the cadence unknown when the read is forbidden", async () => {
    const fetch = vi.fn(async () => {
      throw new ApiError(403, "forbidden");
    });
    vi.spyOn(homeReads, "searchCadence").mockReturnValue({
      key: "/admin/search-cadence?test=forbidden",
      fetch,
    });
    render(<Dashboard now={Date.now()} />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    // 20 min old with the 2 h default: nothing is stale, and every widget states its data time.
    await waitFor(() => expect(notes(/^Data to/)).toHaveLength(3));
    expect(notes(/^Stale:/)).toHaveLength(0);
  });

  it("has no cadence outside a dashboard", () => {
    render(<Probe family="PHASE_A_VOLUME" />);
    expect(screen.getByTestId("PHASE_A_VOLUME")).toHaveTextContent("null/null");
  });
});
