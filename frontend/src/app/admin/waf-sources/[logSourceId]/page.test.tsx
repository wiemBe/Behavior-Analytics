import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WafSourcePage from "./page";
import Loading from "./loading";
import { HISTORY, SCHEMA, catalog, profile, status } from "@/components/fieldmapping/fixtures";
import { ApiError, api, type LogSourceDetail } from "@/lib/api";

const params = (id = "ls-new") => ({ params: Promise.resolve({ logSourceId: id }) });
const NOT_FOUND = () => new ApiError(404, "WAF source profile not found");

const LOG_SOURCE = {
  id: "ls-new", qradar_id: 301, name: "WAF branch", type_name: "Generic WAF DSM", instance_id: "inst-1",
} as LogSourceDetail;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "wafMappingSchema").mockResolvedValue(SCHEMA);
  vi.spyOn(api, "wafPropertyCatalog").mockResolvedValue(catalog());
  vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
  vi.spyOn(api, "wafSources").mockResolvedValue([]);
});

describe("WAF source page", () => {
  it("shows an existing profile with the source as its h1", async () => {
    vi.spyOn(api, "wafProfile").mockResolvedValue(profile());
    render(await WafSourcePage(params("ls-1")));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WAF edge - eu-west");
    expect(screen.getByRole("heading", { name: "Validation" })).toBeInTheDocument();
  });

  it("opens create mode for a source with no profile, prefilled from the latest VALID profile", async () => {
    const seed = profile({ log_source_id: "ls-seed", log_source_name: "WAF edge - eu-west", profile_version: 4,
      mapping: { ...profile().mapping, URL: "request_uri" } });
    vi.spyOn(api, "wafProfile").mockImplementation(async (id: string) => {
      if (id === "ls-seed") return seed;
      throw NOT_FOUND();
    });
    vi.spyOn(api, "logSource").mockResolvedValue(LOG_SOURCE);
    vi.spyOn(api, "wafSources").mockResolvedValue([
      status({ log_source_id: "ls-old", validated_at: "2026-10-01T00:00:00Z" }),
      status({ log_source_id: "ls-seed", validated_at: "2026-10-09T00:00:00Z" }),
    ]);
    render(await WafSourcePage(params()));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WAF branch");
    expect(screen.getByText("This log source has no WAF profile.")).toBeInTheDocument();
    expect(screen.getByText(/Prefilled from WAF edge - eu-west \(version 4\), the most recently validated profile/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /url path property/i })).toHaveValue("request_uri");
  });

  it("says it used the schema defaults when no validated profile exists", async () => {
    vi.spyOn(api, "wafProfile").mockRejectedValue(NOT_FOUND());
    vi.spyOn(api, "logSource").mockResolvedValue(LOG_SOURCE);
    render(await WafSourcePage(params()));
    expect(screen.getByText(/Prefilled from the schema defaults/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /url path property/i })).toHaveValue("URL");
    expect(screen.getByRole("combobox", { name: /virtual host property/i })).toHaveValue("");
  });

  it("falls back to the defaults, and says so, when the seed profile cannot be read", async () => {
    vi.spyOn(api, "wafProfile").mockRejectedValue(NOT_FOUND());
    vi.spyOn(api, "logSource").mockResolvedValue(LOG_SOURCE);
    vi.spyOn(api, "wafSources").mockResolvedValue([status({ log_source_id: "ls-seed" })]);
    render(await WafSourcePage(params()));
    expect(screen.getByText(/Prefilled from the schema defaults/)).toBeInTheDocument();
  });

  it("does not guess the instance when several exist and the log source names none", async () => {
    vi.spyOn(api, "wafProfile").mockRejectedValue(NOT_FOUND());
    vi.spyOn(api, "logSource").mockResolvedValue({ ...LOG_SOURCE, instance_id: undefined });
    vi.spyOn(api, "wafSources").mockResolvedValue([
      status({ log_source_id: "a", instance_id: "i1" }),
      status({ log_source_id: "b", instance_id: "i2" }),
    ]);
    render(await WafSourcePage(params()));
    expect(screen.getByText(/Prefilled from the schema defaults/)).toBeInTheDocument();
  });

  it("says a log source that does not exist does not exist", async () => {
    vi.spyOn(api, "wafProfile").mockRejectedValue(NOT_FOUND());
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(404, "not found"));
    render(await WafSourcePage(params()));
    expect(screen.getByRole("alert")).toHaveTextContent("This log source does not exist.");
  });

  it("explains a failed profile read without leaking the error", async () => {
    vi.spyOn(api, "wafProfile").mockRejectedValue(new ApiError(500, "psycopg2 trace"));
    render(await WafSourcePage(params("ls-1")));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be shown/i);
    expect(alert).toHaveTextContent(/nothing was changed/i);
    expect(alert).not.toHaveTextContent("psycopg2");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("names the permission on a 403", async () => {
    vi.spyOn(api, "wafProfile").mockRejectedValue(new ApiError(403, "forbidden"));
    render(await WafSourcePage(params("ls-1")));
    expect(screen.getByRole("alert")).toHaveTextContent("admin:waf");
  });

  it("explains a failed schema read", async () => {
    vi.spyOn(api, "wafMappingSchema").mockRejectedValue(new ApiError(500, "x"));
    render(await WafSourcePage(params("ls-1")));
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be shown/i);
  });

  it("still shows the editor, in free-text mode, when the catalog read fails", async () => {
    vi.spyOn(api, "wafProfile").mockResolvedValue(profile());
    vi.spyOn(api, "wafPropertyCatalog").mockRejectedValue(new ApiError(500, "x"));
    render(await WafSourcePage(params("ls-1")));
    expect(screen.getByText(/property list is unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /url path property/i })).toBeInTheDocument();
  });

  it("says the history could not be loaded when that read fails", async () => {
    vi.spyOn(api, "wafProfile").mockResolvedValue(profile());
    vi.spyOn(api, "wafProfileHistory").mockRejectedValue(new ApiError(500, "x"));
    render(await WafSourcePage(params("ls-1")));
    expect(screen.getByText(/history could not be loaded/i)).toBeInTheDocument();
  });

  it("has a loading state", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading this waf source/i);
  });
});
