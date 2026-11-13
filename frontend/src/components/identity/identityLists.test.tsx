// Identity in the surfaces other families share: the anomaly list and its filters, the queue
// family, the triage family, the quiet-period mapping (FIX-62 open point) and the navigation.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "@/app/anomalies/page";
import { visibleNav } from "@/components/auth/access";
import { AnomalyFilters, chipLabel } from "@/components/behavior/AnomalyFilters";
import { api } from "@/lib/api";
import { parseSubjectQuery, subjectKindLabel, subjectKindsForApi } from "@/lib/anomalyQuery";
import { NAV, activeNavHref } from "@/lib/nav";
import { parseQueueQuery, queueApiQuery } from "@/lib/queue";
import {
  alertFamily,
  alertTarget,
  allowsWholeSource,
  detectorGroup,
} from "@/lib/suppression";
import { anomalyTarget } from "@/lib/suppressTarget";
import { triageFamilyOf } from "@/lib/triage";

import { summary } from "./fixtures";

const empty = {
  active_only: false, log_source_id: "", instance_id: "", anomaly_type: "", state: "",
  severity: "", evidence_status: "", since: "", until: "",
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the anomaly list's identity filter", () => {
  it("parses the subject kind and an identity detector, and drops an unknown one", () => {
    expect(parseSubjectQuery({ subject_kind: "IDENTITY_SOURCE" }).subject_kind).toBe("IDENTITY_SOURCE");
    expect(parseSubjectQuery({ anomaly_type: "IDENTITY_LOCKOUT_SPIKE" }).identity_anomaly_type).toBe(
      "IDENTITY_LOCKOUT_SPIKE",
    );
    expect(parseSubjectQuery({ anomaly_type: "IDENTITY_DROP_TABLE" }).identity_anomaly_type).toBe("");
    expect(subjectKindLabel("IDENTITY_SOURCE")).toBe("Identity sources");
    expect(subjectKindsForApi("IDENTITY_SOURCE")).toBe("IDENTITY_SOURCE");
  });

  it("offers the seven identity detectors by name in their own group", () => {
    render(<AnomalyFilters values={empty} sources={[]} />);
    const group = screen.getByRole("group", { name: "Identity" });
    expect(within(group).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Audit log cleared",
      "Privileged group membership added",
      "New privileged account",
      "Failed logon spike",
      "Account lockout spike",
      "Account created",
      "Account deleted",
    ]);
  });

  it("names an identity detector in its chip", () => {
    expect(chipLabel("anomaly_type", "IDENTITY_FAILED_LOGON_SPIKE", [])).toBe("Detector: Failed logon spike");
    expect(chipLabel("subject_kind", "IDENTITY_SOURCE", [])).toBe("Subject: Identity sources");
  });

  it("asks the API for identity incidents and renders a row with the source, event and unit", async () => {
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    const list = vi
      .spyOn(api, "anomalies")
      .mockResolvedValue({ items: [summary()], total: 1, limit: 25, offset: 0 });
    render(
      await AnomaliesPage({
        searchParams: Promise.resolve({ subject_kind: "IDENTITY_SOURCE", anomaly_type: "IDENTITY_FAILED_LOGON_SPIKE" }),
      }),
    );
    expect(list.mock.calls[0][0]).toMatchObject({
      subject_kind: "IDENTITY_SOURCE",
      anomaly_type: "IDENTITY_FAILED_LOGON_SPIKE",
    });
    const link = screen.getByRole("link", { name: "dc-01.example.test" });
    expect(link).toHaveAttribute(
      "href",
      "/behavior/identity/sources/11111111-1111-4111-8111-111111111111?event=4625",
    );
    const row = link.closest("tr")!;
    expect(within(row).getByText("4625 Failed logon")).toBeInTheDocument();
    expect(within(row).getByText("events / 5 min")).toBeInTheDocument();
    expect(within(row).getAllByText("Failed logon spike").length).toBeGreaterThan(0);
  });

  it("marks a truncated count in a list row as at least and never draws a missing one as 0", async () => {
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    vi.spyOn(api, "anomalies").mockResolvedValue({
      items: [summary({ observed_measurement: "LOWER_BOUND" }), summary({ id: "a-2", observed_value: null, expected_value: null })],
      total: 2, limit: 25, offset: 0,
    });
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText("Truncated bucket: at least this many events.")).toBeInTheDocument();
    expect(screen.getAllByText(/≥ 180/).length).toBeGreaterThan(0);
    const figures = [...document.querySelectorAll(".ips-figures")].map((e) => e.textContent);
    expect(figures).toContain("— → —");
    expect(figures.every((f) => f !== "0 → 0")).toBe(true);
  });
});

describe("queue and triage", () => {
  it("accepts the identity family in the URL and sends it to the API", () => {
    const q = parseQueueQuery({ family: ["identity", "nope"] });
    expect(q.families).toEqual(["identity"]);
    expect(q.ignored).toEqual(["family"]);
    expect(queueApiQuery(q, undefined)).toContain("family=identity");
  });

  it("keys an identity incident's triage by the identity family", () => {
    expect(triageFamilyOf({ subject_kind: "IDENTITY_SOURCE" })).toBe("identity");
  });
});

describe("quiet periods for identity (FIX-62 open point)", () => {
  it("maps an identity_source alert and an identity anomaly to the identity family", () => {
    expect(alertFamily({ source_type: "identity_source" })).toBe("identity");
    expect(
      alertTarget({ id: "al", source_type: "identity_source", source_anomaly_ids: ["an"], title_params: {} }),
    ).toMatchObject({ via: "alert", family: "identity", anomalyId: "an" });
    expect(anomalyTarget({ id: "an", subject_kind: "IDENTITY_SOURCE" })).toEqual({
      via: "anomaly",
      family: "identity",
      anomalyId: "an",
    });
  });

  it("allows the whole source and words the detector from the identity group", () => {
    expect(allowsWholeSource("identity")).toBe(true);
    expect(allowsWholeSource("property_watch")).toBe(false);
    expect(detectorGroup("identity")).toBe("identityAnomalyType");
    expect(detectorGroup("property_watch")).toBe("propertyWatchAnomalyType");
    expect(detectorGroup("waf")).toBe("anomalyType");
  });
});

describe("navigation", () => {
  it("lists Identity in the Behavior group and owns its detail route", () => {
    const item = NAV[1].items.find((i) => i.label === "Identity");
    expect(item).toMatchObject({ href: "/behavior/identity", permission: "read:identity" });
    expect(activeNavHref("/behavior/identity/sources/abc")).toBe("/behavior/identity");
  });

  it("shows it only to a caller who holds read:identity, wildcards included", () => {
    const labels = (granted: string[]) =>
      visibleNav(NAV, granted).flatMap((g) => g.items.map((i) => i.label));
    expect(labels(["read:anomalies"])).not.toContain("Identity");
    expect(labels(["read:identity"])).toContain("Identity");
    expect(labels(["read:*"])).toContain("Identity");
    expect(labels(["admin:identity"])).not.toContain("Identity");
  });
});

describe("server pages and client modules", () => {
  it("keeps the admin grant out of the client panel, whose exports a server page cannot read as values", async () => {
    const fs = await import("node:fs");
    const page = fs.readFileSync("src/app/behavior/identity/sources/[id]/page.tsx", "utf8");
    expect(page).not.toMatch(/ADMIN_PERMISSION[^;]*from "@\/components\/identity\/IdentityProfilePanel"/);
    expect(page).toMatch(/ADMIN_PERMISSION,[\s\S]*from "@\/components\/identity\/identityFormat"/);
  });
});
