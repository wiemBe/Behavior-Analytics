import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import IpsSourcePage from "./page";
import Loading from "./loading";
import { HISTORY, IPS_SCHEMA, catalog, profile } from "../fixtures";
import { ApiError, api, ipsApi, type LogSourceDetail } from "@/lib/api";

const params = (id = "ls-new") => ({ params: Promise.resolve({ logSourceId: id }) });
const NOT_FOUND = () => new ApiError(404, "IPS source profile not found");

const LOG_SOURCE = {
  id: "ls-new", qradar_id: 412, name: "PaSeries @ pa-fw-01", type_name: "Palo Alto PA Series", instance_id: "inst-1",
} as LogSourceDetail;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(ipsApi, "mappingSchema").mockResolvedValue(IPS_SCHEMA);
  vi.spyOn(ipsApi, "propertyCatalog").mockResolvedValue(catalog());
  vi.spyOn(ipsApi, "profileHistory").mockResolvedValue(HISTORY);
});

describe("IPS source page", () => {
  it("shows an existing profile with the source as its h1", async () => {
    vi.spyOn(ipsApi, "profile").mockResolvedValue(profile());
    render(await IpsSourcePage(params("ls-413")));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("CiscoFirepowerThreatDefense @ ftd-01");
    expect(screen.getByRole("heading", { name: "Validation" })).toBeInTheDocument();
  });

  it("opens create mode for a source with no profile, offering the presets", async () => {
    vi.spyOn(ipsApi, "profile").mockRejectedValue(NOT_FOUND());
    vi.spyOn(api, "logSource").mockResolvedValue(LOG_SOURCE);
    render(await IpsSourcePage(params()));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("PaSeries @ pa-fw-01");
    expect(screen.getByText("This log source has no IPS profile.")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Palo Alto THREAT" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /source ip qradar field/i })).toHaveValue("sourceip");
  });

  it("still shows the editor when the property list and history cannot be read", async () => {
    vi.spyOn(ipsApi, "profile").mockResolvedValue(profile());
    vi.spyOn(ipsApi, "propertyCatalog").mockRejectedValue(new ApiError(502, "upstream"));
    vi.spyOn(ipsApi, "profileHistory").mockRejectedValue(new ApiError(500, "boom"));
    render(await IpsSourcePage(params("ls-413")));
    expect(screen.getByText(/The property list is unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/The history could not be loaded/)).toBeInTheDocument();
  });

  it("says an unknown log source does not exist", async () => {
    vi.spyOn(ipsApi, "profile").mockRejectedValue(NOT_FOUND());
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(404, "not found"));
    render(await IpsSourcePage(params("nope")));
    expect(screen.getByRole("alert")).toHaveTextContent("This log source does not exist.");
    expect(screen.getByRole("alert")).toHaveTextContent(/Choose one from the IPS sources list/);
    expect(screen.queryByRole("heading", { name: "Field mapping" })).not.toBeInTheDocument();
  });

  it("names admin:ips on a 403 reading the profile", async () => {
    vi.spyOn(ipsApi, "profile").mockRejectedValue(new ApiError(403, "forbidden"));
    render(await IpsSourcePage(params("ls-413")));
    expect(screen.getByRole("alert")).toHaveTextContent("admin:ips");
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing was changed.");
  });

  it("names admin:ips on a 403 reading the schema", async () => {
    vi.spyOn(ipsApi, "mappingSchema").mockRejectedValue(new ApiError(403, "forbidden"));
    render(await IpsSourcePage(params("ls-413")));
    expect(screen.getByRole("alert")).toHaveTextContent("admin:ips");
  });

  it("explains a failed read without leaking the error", async () => {
    vi.spyOn(ipsApi, "profile").mockRejectedValue(new ApiError(500, "psycopg2 trace"));
    render(await IpsSourcePage(params("ls-413")));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be shown/);
    expect(alert).toHaveTextContent(/reload/i);
    expect(alert).not.toHaveTextContent("psycopg2");
  });

  it("explains a failed log-source lookup in create mode", async () => {
    vi.spyOn(ipsApi, "profile").mockRejectedValue(NOT_FOUND());
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(500, "boom"));
    render(await IpsSourcePage(params()));
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be shown/);
  });

  it("has a loading state that names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading this ips source/i);
  });
});
