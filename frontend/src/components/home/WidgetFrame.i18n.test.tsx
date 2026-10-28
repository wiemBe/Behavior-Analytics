// The shared Home frame in Turkish: every state it draws (loading, forbidden, error, no data,
// not collected, stale, the data time, a failed refresh), the four honesty qualifiers in both
// densities, the range control and Expand. The data hook is replaced; English is the default of
// the harness, so each test opts in with setTestLocale("tr").

import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";
import type { SearchCadenceItem } from "@/lib/api";
import { insufficient, notCollected, partial, truncated, worded, type Qualifier } from "@/lib/home/qualifiers";
import { CadenceProvider } from "@/widgets/cadence";
import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";
import { setTestLocale } from "../../../vitest.setup";

import { secondary, WidgetFrame, WidgetQualifiers, type DataTo } from "./WidgetFrame";
import { reasonText, badgePhrase } from "./qualifierText";
import { useMeasures } from "@/i18n/measures";
import { createLocaleTranslator } from "@/i18n/testTranslator";

const res = vi.hoisted(() => ({ cadence: { status: "loading" } as unknown }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: () => res.cadence as ResourceState<unknown>,
}));

const NOW = Date.parse("2026-10-22T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();
const note = (re: RegExp) =>
  screen.getByText((_c, el) => el?.tagName === "P" && re.test(el.textContent ?? ""));

const answer = (over: Partial<Extract<ResourceState<string>, { status: "ok" }>> = {}) =>
  ({
    status: "ok",
    data: "the figures",
    receivedAt: "2026-10-22T11:59:30Z",
    refreshing: false,
    refreshFailed: null,
    ...over,
  }) as ResourceState<string>;

const cadenceItem = (cadence: number): SearchCadenceItem => ({
  family: "PHASE_A_VOLUME",
  cadence_seconds: cadence,
  allowed_cadences_seconds: [cadence],
  bucket_seconds: 300,
  settle_seconds: 0,
  last_slot_start: null,
  next_dispatch_at: "2026-10-22T12:05:00Z",
  updated_at: "2026-10-22T00:00:00Z",
  updated_by: null,
  last_run: null,
});

function frame(
  state: ResourceState<string> | Parameters<typeof WidgetFrame<string>>[0]["state"],
  props: { dataTo?: DataTo; variant?: "panel" | "kpi"; badge?: string } = {},
) {
  return render(
    <CadenceProvider>
      <WidgetFrame
        id="f"
        title="Filo EPS"
        link={{ href: "/fleet", label: "Filo" }}
        what="filo EPS"
        permission="read:fleet"
        state={state as ResourceState<string>}
        variant={props.variant}
        dataTo={props.dataTo}
        badge={props.badge}
      >
        {({ data }) => <p>{data}</p>}
      </WidgetFrame>
    </CadenceProvider>,
  );
}

beforeEach(() => {
  setTestLocale("tr");
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  res.cadence = { status: "ok", data: [cadenceItem(300)] };
});
afterEach(() => vi.useRealTimers());

describe("WidgetFrame states in Turkish", () => {
  it.each([{ status: "idle" }, { status: "loading" }] as ResourceState<string>[])(
    "says loading ($status) as polite text",
    (state) => {
      frame(state);
      expect(screen.getByRole("status")).toHaveTextContent("Filo EPS yükleniyor…");
      expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    },
  );

  it("says forbidden with the permission, in both densities", () => {
    frame({ status: "forbidden", httpStatus: 403 });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Rolünüz bunu okuyamıyor: filo EPS (izin: read:fleet). Hiçbir şey değiştirilmedi.",
    );
  });

  it("says what failed and what to do, with the status, and never the exception", () => {
    frame({ status: "error", httpStatus: 503 });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Okunamadı: filo EPS.");
    expect(alert).toHaveTextContent("Backend HTTP 503 ile yanıt verdi.");
    expect(alert).toHaveTextContent("Üst çubuktaki Yenile'yi kullanın");
  });

  it("strips the English article from a subject only where one is written", () => {
    render(
      <WidgetFrame
        id="g"
        title="T"
        link={{ href: "/x", label: "L" }}
        what="the fleet state"
        permission="read:fleet"
        state={{ status: "error", httpStatus: null }}
      >
        {() => null}
      </WidgetFrame>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Okunamadı: fleet state.");
    expect(screen.getByRole("alert")).toHaveTextContent("Backend yanıt vermedi.");
  });

  it("says no data and not collected with their Turkish leads", () => {
    const view = frame({ kind: "no_data", reason: "Neden." } as never);
    expect(screen.getByRole("status")).toHaveTextContent("Veri yok. Neden.");
    view.unmount();
    frame({ kind: "not_collected", reason: "Neden." } as never);
    expect(screen.getByRole("status")).toHaveTextContent("Toplanmadı. Neden.");
  });

  it("marks old data as stale with its UTC time and age, and still draws it", () => {
    frame(answer(), { dataTo: { at: ago(3 * 3600), family: "PHASE_A_VOLUME" } });
    expect(screen.getByText("the figures")).toBeInTheDocument();
    expect(note(/Güncel değil: son veri 09:00:00 UTC tarihli, 3\u00a0sa önce\./)).toBeInTheDocument();
  });

  it("says when the age of the data cannot be read", () => {
    frame(answer(), { dataTo: { at: "not a time", family: "PHASE_A_VOLUME" } });
    expect(screen.getByText("Güncel değil: son verinin ulaştığı zaman okunamıyor.")).toBeInTheDocument();
  });

  it("states where fresh data reaches, with the age after the UTC time", () => {
    frame(answer(), { dataTo: { at: ago(240), family: "PHASE_A_VOLUME" } });
    expect(note(/^Son veri: 11:56:00 UTC, 4\u00a0dk önce\.$/)).toBeInTheDocument();
  });

  it("keeps the previous answer on a failed refresh and says so, in both densities", () => {
    const refreshFailed = { httpStatus: 503 };
    const view = frame(answer({ refreshFailed }), { dataTo: { at: ago(240), family: "PHASE_A_VOLUME" } });
    expect(note(/Son yenileme başarısız oldu \(HTTP 503\)\. 11:56:00 UTC tarihli veri gösteriliyor\./)).toBeInTheDocument();
    view.unmount();
    frame(answer({ refreshFailed }), { variant: "kpi" });
    expect(note(/Yenileme başarısız oldu; 11:59 UTC saatinde alınan veri gösteriliyor\./)).toBeInTheDocument();
  });

  it("words the Expand control and names the widget in it", () => {
    const runtime: WidgetRuntime = {
      rangeOptions: ["1h", "24h", "7d"],
      override: null,
      dashboardRange: "24h",
      setRange: vi.fn(),
      onExpand: vi.fn(),
      expanded: false,
    };
    render(
      <WidgetRuntimeContext.Provider value={runtime}>
        <WidgetFrame
          id="r"
          title="Filo EPS"
          link={{ href: "/fleet", label: "Filo" }}
          what="filo EPS"
          permission="read:fleet"
          state={answer()}
        >
          {() => null}
        </WidgetFrame>
      </WidgetRuntimeContext.Provider>,
    );
    expect(screen.getByRole("button", { name: "Filo EPS bileşenini büyüt" })).toHaveTextContent("Büyüt");
    const group = screen.getByRole("group", { name: "Zaman aralığı" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["1\u00a0sa", "24\u00a0sa", "7\u00a0gün"]);
    expect(screen.getByLabelText("Filo EPS için zaman aralığı")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Filo EPS bileşenini büyüt" }));
    expect(runtime.onExpand).toHaveBeenCalled();
  });
});

describe("WidgetQualifiers in Turkish", () => {
  const all: Qualifier[] = [
    insufficient("No class has a complete bucket in this window.", { code: "NO_CLASS_COMPLETE" }),
    partial(270, 288, "intervals"),
    truncated(200, 345),
    notCollected("No offense snapshot has been collected yet.", { code: "NO_SNAPSHOT" }),
  ];

  it("words the four pills and the standard sentences in the panel form", () => {
    render(<WidgetQualifiers qualifiers={all} />);
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual([
      "Yetersiz veri: Bu aralıkta tamamlanmış zaman dilimi olan sınıf yok.",
      "Kısmi: 270\u00a0/\u00a0288 aralık toplandı",
      "Kırpılmış: İlk 200\u00a0/\u00a0345",
      "Toplanmadı: Henüz offense kesiti toplanmadı.",
    ]);
  });

  it("words the compact KPI form of the same qualifiers", () => {
    const { container } = render(<WidgetQualifiers variant="kpi" qualifiers={[all[1], all[2]]} />);
    expect(container.textContent).toBe("Kısmi270\u00a0/\u00a0288 aralıkKırpılmış" + "İlk 200\u00a0/\u00a0345");
  });

  it("shows a widget's own, already translated sentence as it is", () => {
    const own = worded<Qualifier>({ kind: "PARTIAL", have: 1, of: 2, unit: "x", text: "Kendi cümlesi." });
    render(<WidgetQualifiers qualifiers={[own]} />);
    expect(screen.getByRole("listitem")).toHaveTextContent("Kısmi: Kendi cümlesi.");
  });

  it("translates a reason by its code and values, and shows a reason with no code as it is", () => {
    const say = createLocaleTranslator("tr");
    expect(reasonText(say, notCollected("x", { code: "NO_STEP_COMPLETE", values: { total: 1200 } }))).toBe(
      "1.200 adımın hiçbirinde scope'taki tüm kaynak aralıkları tamamlanmış değil.",
    );
    expect(reasonText(say, insufficient("Something nobody coded."))).toBe("Something nobody coded.");
    // Text that happens to equal a known English sentence is not translated: only a code is.
    expect(reasonText(say, insufficient("No class has a complete bucket in this window."))).toBe(
      "No class has a complete bucket in this window.",
    );
  });

  it("words the header pill for the worst caveat", () => {
    function Probe({ qualifiers }: { qualifiers: Qualifier[] }) {
      const measures = useMeasures();
      return <p>{badgePhrase(createLocaleTranslator("tr"), measures, qualifiers)}</p>;
    }
    const { container, rerender } = render(<Probe qualifiers={all} />);
    expect(container.textContent).toBe("Toplanmadı");
    rerender(<Probe qualifiers={all.slice(0, 3)} />);
    expect(container.textContent).toBe("Yetersiz veri");
    rerender(<Probe qualifiers={all.slice(1, 3)} />);
    expect(container.textContent).toBe("Kırpılmış: ilk 200\u00a0/\u00a0345");
    rerender(<Probe qualifiers={all.slice(1, 2)} />);
    expect(container.textContent).toBe("Kısmi: 270\u00a0/\u00a0288 aralık toplandı");
    rerender(<Probe qualifiers={[]} />);
    expect(container.textContent).toBe("");
  });
});

describe("secondary reads in Turkish", () => {
  it("names why a secondary read is missing", () => {
    const say = createLocaleTranslator("tr");
    expect(secondary({ status: "forbidden", httpStatus: 403 }, say).gap).toBe("rolünüz bunu okuyamıyor");
    expect(secondary({ status: "error", httpStatus: 500 }, say).gap).toBe("okunamadı");
    expect(secondary({ status: "loading" }, say).pending).toBe(true);
    expect(secondary({ status: "forbidden", httpStatus: 403 }).gap).toBe("your role cannot read it");
  });
});
