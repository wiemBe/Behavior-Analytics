// The catch-all senders route in Turkish: the page and its route states, the queue with every
// state it has, the per-source volume view, and the helpers the three share.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../../vitest.setup";
import { ApiError, catchallApi, type CatchallSender } from "@/lib/api";
import { fleetTextOf } from "@/lib/fleetText";
import { createTextTranslator } from "@/i18n/textTranslator";

import tr from "../../../../messages/tr";
import {
  RANGES,
  STATUS_LABELS,
  admissionSentences,
  bucketUnit,
  deniedNote,
  measurementText,
  rangeLabel,
  senderFailure,
  statusFilterLabel,
  statusLabel,
  type Slot,
} from "./catchall";
import CatchallError from "./error";
import { bucket, sender, sources } from "./fixtures";
import Loading from "./loading";
import CatchallPage from "./page";
import { SendersQueue } from "./SendersQueue";
import { SourceVolume } from "./SourceVolume";

const NB = " ";
const turkish = fleetTextOf(createTextTranslator("tr", tr), "tr");

let lastProps: { slots: Slot[]; selected: number | null; onSelect: (i: number) => void } | null = null;
vi.mock("./BucketChart", () => ({
  BucketChart: (props: NonNullable<typeof lastProps> & { textSummary: string }) => {
    lastProps = props;
    return (
      <div role="img" aria-label="chart">
        {props.textSummary}
      </div>
    );
  },
}));

const NOW = "2026-10-13T10:30:00Z";
const rows = () => [
  bucket(0),
  bucket(5, { measurement: "LOWER_BOUND", events: 1900, distinct_senders_lower_bound: true }),
  bucket(20, { events: 1_204_000, untracked_senders: 1204, untracked_events: 7 }),
];
const list = (items = rows()) => ({ catchall_log_source_id: "x", since: "", until: "", items });
const page = (items: CatchallSender[], total = items.length) => ({ items, total, limit: 50, offset: 0 });

beforeEach(() => {
  vi.restoreAllMocks();
  lastProps = null;
  setTestLocale("tr");
  vi.spyOn(catchallApi, "sources").mockResolvedValue(sources());
});

describe("/fleet/catchall page in Turkish", () => {
  it("titles the page, says what a sender is, and opens with the queue", async () => {
    vi.spyOn(catchallApi, "senders").mockResolvedValue(page([sender()]));
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(await CatchallPage());
    expect(screen.getByRole("heading", { level: 1, name: "Catch-all gönderenleri" })).toBeInTheDocument();
    expect(screen.getByText(/Gönderen, bir catch-all kaynağına düşen olaylar/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Gönderen kuyruğu" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Kaynağa göre hacim" })).toBeInTheDocument();
  });

  it("names the catch-all types when the inventory holds no catch-all source", async () => {
    vi.mocked(catchallApi.sources).mockResolvedValue(sources({ items: [] }));
    render(await CatchallPage());
    expect(screen.getByText("Envanterde catch-all kaynağı yok.")).toBeInTheDocument();
    expect(screen.getByText(/şunlardan biri olduğunda catch-all sayılır: 39, 67, 212, 367, 4000\./)).toBeInTheDocument();
  });

  it("falls back to the words for the configured types when the API names none", async () => {
    vi.mocked(catchallApi.sources).mockResolvedValue(sources({ items: [], catchall_type_ids: [] }));
    render(await CatchallPage());
    expect(screen.getByText(/catch-all sayılır: yapılandırılmış catch-all türleri\./)).toBeInTheDocument();
  });

  it("names the permission on a 403, asks for a new sign-in on a 401, and says what to do otherwise", async () => {
    vi.mocked(catchallApi.sources).mockRejectedValueOnce(new ApiError(403, "no"));
    const first = render(await CatchallPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Catch-all gönderenleri gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("izin: read:fleet");
    first.unmount();
    vi.mocked(catchallApi.sources).mockRejectedValueOnce(new ApiError(401, "no"));
    const second = render(await CatchallPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Oturumunuzun süresi doldu.");
    second.unmount();
    vi.mocked(catchallApi.sources).mockRejectedValueOnce(new Error("boom"));
    render(await CatchallPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Catch-all gönderenleri yüklenemedi. Backend'e erişilemiyor olabilir");
    expect(screen.getByRole("alert")).not.toHaveTextContent("boom");
  });

  it("announces loading and the route error", () => {
    const { unmount } = render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Catch-all gönderenleri yükleniyor…");
    unmount();
    render(<CatchallError error={Object.assign(new Error("x"), { digest: "d1" })} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Catch-all gönderenleri sayfası gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Referans: d1.");
  });
});

describe("the senders queue in Turkish", () => {
  it("labels the filters, the columns and the counts, with the Turkish number form", () => {
    render(
      <SendersQueue
        sources={sources({ items: sources().items.map((s) => ({ ...s, new_sender_count: 1204 })) })}
        initial={page([sender({ events_24h: 18420 })], 1204)}
      />,
    );
    expect(screen.getByRole("search", { name: "Gönderenleri filtrele" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "İnceleme durumu" })).toHaveValue("NEW");
    expect(screen.getByRole("option", { name: "Yeni (2.408)" })).toBeInTheDocument();
    for (const name of ["Yoksayıldı", "İncelendi", "Tüm durumlar"]) {
      expect(screen.getByRole("option", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("option", { name: "Tüm catch-all kaynakları" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Gönderen ara, örn. fw-edge")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Listeyi yeniden yükle" })).toBeInTheDocument();
    for (const name of ["Gönderen", "Catch-all kaynağı", "Görülme (UTC)", /^24.sa olay$/, "İnceleme", "İşlemler"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("status", { name: "" })).toBeTruthy();
    expect(document.body.textContent).toContain(`1 / 1.204 gönderen gösteriliyor.`.replace(/ \/ /, `${NB}/${NB}`));
    expect(screen.getByText("18.420")).toBeInTheDocument();
    expect(document.body.textContent).toContain(`18.420${NB}olay`);
  });

  it("shows unknown, never 0, and keeps the lower-bound note on the figure", () => {
    render(<SendersQueue sources={sources()} initial={page([sender({ events_24h: null })])} />);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByText("bilinmiyor")).toBeInTheDocument();
    expect(row).not.toHaveTextContent(/\b0\b/);
    expect(screen.getAllByTitle(/Bir alt sınırdır/).length).toBeGreaterThan(0);
    expect(screen.getByText("bilinmiyor", { selector: "em" })).toBeInTheDocument();
  });

  it("names who decided a reviewed sender, and says when that was not recorded", () => {
    render(
      <SendersQueue
        sources={sources()}
        initial={page([
          sender({ id: "a", review_status: "REVIEWED", reviewed_by: "s.okafor", reviewed_at: "2026-10-13T08:02:00Z" }),
          sender({ id: "b", identifier: "b-host", review_status: "DISMISSED", reviewed_by: null, reviewed_at: null }),
          sender({ id: "c", identifier: "c-host", active: false }),
        ])}
      />,
    );
    expect(screen.getByText(/s\.okafor tarafından/)).toBeInTheDocument();
    expect(screen.getByText("İnceleyen kaydedilmedi")).toBeInTheDocument();
    expect(screen.getByText("Yakın zamanda görülmedi")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "fw-edge-03 göndereni için yoksay" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "c-host göndereni için incele" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "fw-edge-03 göndereni için incele" })).toBeNull();
  });

  it("words the outcome of a review and a dismissal", async () => {
    vi.spyOn(catchallApi, "review").mockResolvedValue(sender({ review_status: "REVIEWED", reviewed_by: "me", reviewed_at: "2026-10-13T08:02:00Z" }));
    vi.spyOn(catchallApi, "dismiss").mockResolvedValue(sender({ id: "s2", identifier: "d-host", review_status: "DISMISSED", reviewed_by: "me", reviewed_at: "2026-10-13T08:02:00Z" }));
    render(<SendersQueue sources={sources()} initial={page([sender(), sender({ id: "s2", identifier: "d-host" })])} />);
    await userEvent.click(screen.getByRole("button", { name: "fw-edge-03 göndereni için incele" }));
    expect(await screen.findByText("İncelendi olarak işaretlendi. Liste yeniden yüklendiğinde Yeni kuyruğundan çıkar.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "d-host göndereni için yoksay" }));
    expect(await screen.findByText("Yoksayıldı. Liste yeniden yüklendiğinde Yeni kuyruğundan çıkar.")).toBeInTheDocument();
  });

  it("names the permission when a role cannot review, and disables both actions with the reason", async () => {
    vi.spyOn(catchallApi, "review").mockRejectedValue(new ApiError(403, "no"));
    render(<SendersQueue sources={sources()} initial={page([sender()])} />);
    await userEvent.click(screen.getByRole("button", { name: "fw-edge-03 göndereni için incele" }));
    expect(await screen.findAllByText(/izin: log_source:write/)).not.toHaveLength(0);
    expect(
      screen.getByText(/İncele ve Yoksay devre dışı: bunlar için/).textContent,
    ).toBe("İncele ve Yoksay devre dışı: bunlar için log_source:write izni gerekir, rolünüzde bu izin yok.");
    expect(screen.getByRole("button", { name: "fw-edge-03 göndereni için yoksay" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "fw-edge-03 göndereni için yoksay" })).toHaveAttribute(
      "title",
      "İncele ve Yoksay için log_source:write izni gerekir, rolünüzde bu izin yok. Kuyruk sizin için salt okunur.",
    );
  });

  it("says nothing changed when the sender no longer exists", async () => {
    vi.spyOn(catchallApi, "dismiss").mockRejectedValue(new ApiError(404, "gone"));
    render(<SendersQueue sources={sources()} initial={page([sender()])} />);
    await userEvent.click(screen.getByRole("button", { name: "fw-edge-03 göndereni için yoksay" }));
    expect(await screen.findByText("Değiştirilmedi. Bu gönderen artık yok; listeyi yeniden yükleyin.")).toBeInTheDocument();
  });

  it("shows loading, then the error with a retry that keeps the filters", async () => {
    let fail: (e: unknown) => void = () => {};
    vi.spyOn(catchallApi, "senders").mockReturnValueOnce(new Promise((_, reject) => (fail = reject)));
    render(<SendersQueue sources={sources()} initial={page([sender()])} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "İnceleme durumu" }), "ALL");
    expect(await screen.findByText("Gönderenler yükleniyor…")).toBeInTheDocument();
    await act(async () => fail(new Error("down")));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Gönderenler yüklenemedi.");
    expect(alert).toHaveTextContent("Filtreleriniz korunur.");
    expect(within(alert).getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument();
  });

  it("says forbidden with the permission and offers no retry", async () => {
    vi.spyOn(catchallApi, "senders").mockRejectedValue(new ApiError(403, "no"));
    render(<SendersQueue sources={sources()} initial={page([sender()])} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "İnceleme durumu" }), "REVIEWED");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Gönderenler gösterilemiyor.");
    expect(alert).toHaveTextContent("izin: read:fleet");
    expect(within(alert).queryByRole("button")).toBeNull();
  });

  it("tells the four empty states apart, in words", async () => {
    // never collected
    const never = sources({ items: sources().items.map((s) => ({ ...s, latest_bucket_start: null })) });
    const a = render(<SendersQueue sources={never} initial={page([])} />);
    expect(screen.getByText("Henüz hiçbir şey toplanmadı.")).toBeInTheDocument();
    expect(screen.getByText(/boş bir liste kimin gönderdiği hakkında bir şey söylemez/)).toBeInTheDocument();
    a.unmount();
    // clear, with how far collection got
    const b = render(<SendersQueue sources={sources()} initial={page([])} />);
    expect(screen.getByText("Kuyruk boş.")).toBeInTheDocument();
    expect(document.body).toHaveTextContent(
      "İnceleme bekleyen gönderen yok. Ölçülen son zaman dilimi 2026-10-13 17:30:00 UTC zamanında başlıyor.",
    );
    b.unmount();
    // another status
    vi.spyOn(catchallApi, "senders").mockResolvedValue(page([]));
    render(<SendersQueue sources={sources()} initial={page([sender()])} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "İnceleme durumu" }), "DISMISSED");
    expect(await screen.findByText("Durumu yoksayıldı olan gönderen yok.")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "İnceleme durumu" }), "ALL");
    expect(await screen.findByText("İzlenen gönderen yok.")).toBeInTheDocument();
    expect(screen.getByText("Diğer gönderenleri görmek için başka bir durum seçin.")).toBeInTheDocument();
  });

  it("says a filter matched nothing", async () => {
    vi.spyOn(catchallApi, "senders").mockResolvedValue(page([]));
    render(<SendersQueue sources={sources()} initial={page([sender()])} />);
    await userEvent.type(screen.getByRole("searchbox", { name: "Gönderen ara" }), "zz");
    expect(await screen.findByText("Bu filtrelere uyan gönderen yok.", {}, { timeout: 2000 })).toBeInTheDocument();
  });

  it("loads more, and says when the next page could not be loaded", async () => {
    const items = Array.from({ length: 2 }, (_, i) => sender({ id: `s${i}`, identifier: `host-${i}` }));
    vi.spyOn(catchallApi, "senders").mockRejectedValue(new Error("x"));
    render(<SendersQueue sources={sources()} initial={page(items, 5)} />);
    await userEvent.click(screen.getByRole("button", { name: "Daha fazla gönderen göster" }));
    expect(await screen.findByText("Sonraki gönderenler yüklenemedi. Gösterilenler değişmedi; tekrar deneyin.")).toBeInTheDocument();
  });
});

describe("the volume view in Turkish", () => {
  it("shows loading, then the Turkish sentence of what was and was not measured", async () => {
    let release: (v: ReturnType<typeof list>) => void = () => {};
    vi.spyOn(catchallApi, "buckets").mockReturnValue(new Promise((r) => (release = r)));
    render(<SourceVolume sources={sources()} now={NOW} />);
    expect(screen.getByText("Zaman dilimleri yükleniyor…")).toBeInTheDocument();
    await act(async () => release(list()));
    const line = (await screen.findByText(/Bunlar bilinmiyor, sıfır değil/)).closest("p")!;
    expect(line).toHaveTextContent("Toplama 2026-10-13 10:25:00 UTC zamanına kadar yapıldı.");
    expect(line).toHaveTextContent(/Bundan önceki \d+ zaman dilimi ölçülmedi \(gölgeli\)\. Bunlar bilinmiyor, sıfır değil\./);
    expect(screen.getByRole("group", { name: "Bir kaynak ve bir aralık seçin" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Aralık" })).toBeInTheDocument();
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(
      expect.arrayContaining([`6${NB}saat`, `24${NB}saat`, `7${NB}gün`]),
    );
    expect(screen.getByRole("img", { name: "chart" }).textContent).toContain("Zaman dilimi başına olay (5");
    expect(screen.getByRole("img", { name: "chart" }).textContent).toContain("boşluk olarak gösteriliyor");
    const key = screen.getByRole("list", { name: "Grafik anahtarı" });
    expect(within(key).getByText("Zaman dilimi başına olay")).toBeInTheDocument();
    expect(within(key).getByText(/En az: arama kırpıldı/)).toBeInTheDocument();
    expect(within(key).getByText("Ölçülmedi")).toBeInTheDocument();
  });

  it("describes the selected bucket in words and numbers the Turkish way", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    const panel = await screen.findByRole("group", { name: "Seçili zaman dilimi" });
    expect(panel).toHaveTextContent("2026-10-13 10:20:00 UTC ile 2026-10-13 10:25:00 UTC arası");
    expect(panel.textContent).toContain(`1.204.000${NB}olay / 5${NB}dk`);
    expect(panel).toHaveTextContent("Bu zaman diliminde 1 yeni gönderen kabul edildi");
    expect(panel).toHaveTextContent("2 gönderen kabul eşiğinin altında kaldı");
    expect(panel).toHaveTextContent("Yeni gönderen kotası dolu olduğundan 3 gönderen reddedildi.");
    expect(panel).toHaveTextContent("1.204 gönderen (7 olay) adıyla izlenmiyor.");
    expect(panel).toHaveTextContent("Reddedilen ve geçersiz gönderenler sayılır");
    expect(within(panel).getByText("Kesin")).toBeInTheDocument();
    for (const name of ["Ölçüm", "Olaylar", "Kayıtlar", "Farklı gönderenler"]) {
      expect(within(panel).getByText(name, { selector: "dt" })).toBeInTheDocument();
    }
    expect(within(panel).getByRole("heading", { name: "Kabul aşamasında olanlar" })).toBeInTheDocument();
    expect(within(panel).getByRole("heading", { name: "Bu zaman dilimindeki en yoğun gönderenler" })).toBeInTheDocument();
    expect(within(panel).getByRole("table", { name: /en yoğun izlenen gönderenleri, en çok 20/ })).toBeInTheDocument();
  });

  it("marks a lower-bound bucket as at least, and walks between buckets in words", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    await screen.findByRole("group", { name: "Seçili zaman dilimi" });
    expect(screen.getByRole("button", { name: "Sonraki ölçülmüş zaman dilimi" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Önceki ölçülmüş zaman dilimi" }));
    const panel = screen.getByRole("group", { name: "Seçili zaman dilimi" });
    expect(panel).toHaveTextContent("En az");
    expect(panel).toHaveTextContent("En az (arama kırpıldı; daha fazla olay olabilir)");
    expect(panel.textContent).toContain(`en az 1.900`);
    expect(panel.textContent).toMatch(/en az 4/);
    expect(screen.getByText("Ya da grafikte bir çubuğa tıklayın.")).toBeInTheDocument();
  });

  it("says a selected gap was not measured, and a bucket after the latest one is not known yet", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    await screen.findByRole("group", { name: "Seçili zaman dilimi" });
    act(() => lastProps!.onSelect(lastProps!.slots.findIndex((s) => s.state === "gap")));
    expect(screen.getByRole("group", { name: "Seçili zaman dilimi" })).toHaveTextContent(
      "Bu zaman dilimi ölçülmedi: bunun için kayıt saklanmadı. Hacmi bilinmiyor, sıfır değil.",
    );
    act(() => lastProps!.onSelect(lastProps!.slots.findIndex((s) => s.state === "beyond")));
    expect(screen.getByRole("group", { name: "Seçili zaman dilimi" })).toHaveTextContent(
      "Toplama ona henüz ulaşmadı",
    );
  });

  it("says no bucket was measured in the range, with and without a latest bucket", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list([]));
    const a = render(<SourceVolume sources={sources()} now={NOW} />);
    expect(await screen.findByText("Bu aralıkta hiçbir zaman dilimi ölçülmedi.", { selector: "strong" })).toBeInTheDocument();
    expect(document.body).toHaveTextContent(
      "SIM Generic Log DSM-7 :: qradar-lab için ölçülen son zaman dilimi 2026-10-13 17:30:00 UTC zamanında başlıyor. Ona ulaşmak için daha uzun bir aralık seçin.",
    );
    a.unmount();
    const never = sources({ items: sources().items.map((s) => ({ ...s, latest_bucket_start: null })) });
    render(<SourceVolume sources={never} now={NOW} />);
    expect(await screen.findByText(/için hiç zaman dilimi ölçülmedi\. Buradaki boş grafik toplamanın yokluğudur, sıfır trafik değildir\./)).toBeInTheDocument();
  });

  it("names the permission on a 403 and says what to do on a failure", async () => {
    vi.spyOn(catchallApi, "buckets").mockRejectedValueOnce(new ApiError(403, "no"));
    const a = render(<SourceVolume sources={sources()} now={NOW} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Zaman dilimleri gösterilemiyor.");
    expect(alert).toHaveTextContent("izin: read:fleet");
    a.unmount();
    vi.spyOn(catchallApi, "buckets").mockRejectedValueOnce(new Error("x"));
    render(<SourceVolume sources={sources()} now={NOW} />);
    const second = await screen.findByRole("alert");
    expect(second).toHaveTextContent("Zaman dilimleri yüklenemedi. Backend'e erişilemiyor olabilir");
    await waitFor(() => expect(within(second).getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument());
  });
});

describe("catch-all helpers in both languages", () => {
  it("words the bucket unit with its unit, English unchanged", () => {
    expect([bucketUnit(300), bucketUnit(3600), bucketUnit(45)]).toEqual(["5 min", "1 h", "45 s"]);
    expect([bucketUnit(300, turkish), bucketUnit(3600, turkish), bucketUnit(45, turkish)]).toEqual([
      `5${NB}dk`,
      `1${NB}sa`,
      `45${NB}sn`,
    ]);
  });

  it("has a Turkish word for every review status and range; an unknown status stays as sent", () => {
    for (const s of ["NEW", "REVIEWED", "DISMISSED"] as const) {
      expect(statusLabel(s)).toBe(STATUS_LABELS[s]);
      expect(statusLabel(s, turkish)).not.toBe(statusLabel(s));
    }
    expect(statusFilterLabel("ALL", turkish)).toBe("Tüm durumlar");
    expect(statusLabel("FUTURE" as never, turkish)).toBe("FUTURE");
    expect(RANGES.map((r) => rangeLabel(r))).toEqual(["6 hours", "24 hours", "7 days"]);
    expect(RANGES.map((r) => rangeLabel(r, turkish))).toEqual([`6${NB}saat`, `24${NB}saat`, `7${NB}gün`]);
  });

  it("words the admission counts with plurals in English and the Turkish number form in Turkish", () => {
    const b = bucket(0, { admitted: 1, below_admission_threshold: 1204, rejected_over_quota: 0, untracked_senders: 1, untracked_events: 1 });
    expect(admissionSentences(b)).toEqual([
      "1 new sender was admitted in this bucket and are now tracked by name.",
      "1,204 senders were below the admission threshold and counted only.",
      "0 senders were refused because the new-sender quota was full.",
      "1 sender (1 event) is not tracked by name.",
    ]);
    expect(admissionSentences(b, turkish)[1]).toBe("1.204 gönderen kabul eşiğinin altında kaldı ve yalnızca sayıldı.");
  });

  it("explains every measurement state, and says nothing for an exact one", () => {
    expect(measurementText(bucket(0))).toBeNull();
    expect(measurementText(bucket(0, { measurement: "LOWER_BOUND" }), turkish)).toBe(
      "En az (arama kırpıldı; daha fazla olay olabilir)",
    );
    expect(measurementText(bucket(0, { measurement: "FAILED" }), turkish)).toBe("Ölçülmedi (toplama başarısız oldu)");
    expect(measurementText(bucket(0, { measurement: "NOT_COLLECTED" as never }), turkish)).toBe("Ölçülmedi");
  });

  it("words the denial note and the failures with the permission", () => {
    expect(deniedNote(turkish)).toContain("log_source:write");
    expect(senderFailure(new ApiError(403, "x"), turkish).text).toBe(
      "Rolünüz gönderenleri inceleyemiyor (izin: log_source:write). Hiçbir şey değiştirilmedi.",
    );
    expect(senderFailure(new Error("x"), turkish).text).toMatch(/^Değiştirilmedi\./);
  });
});
