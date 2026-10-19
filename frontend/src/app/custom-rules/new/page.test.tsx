import { cleanup, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NewCustomRulePage from "./page";
import { customRuleLatency } from "@/components/customrules/authoring";
import { catalog, reader, writer } from "@/components/customrules/fixtures";
import { partsText } from "@/components/cadence/CadenceLatency";
import { ApiError, api } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/components/customrules/CustomRuleEditor", () => ({
  CustomRuleEditor: () => <form aria-label="custom rule editor" />,
}));

beforeEach(() => {
  vi.spyOn(api, "customRuleCatalog").mockResolvedValue(catalog());
  vi.spyOn(api, "caller").mockResolvedValue(writer);
});

describe("new custom rule page", () => {
  it("shows the editor and latency statement to a writer", async () => {
    render(await NewCustomRulePage());
    expect(screen.getByRole("heading", { name: "New custom rule", level: 1 })).toBeInTheDocument();
    expect(screen.getByLabelText("custom rule editor")).toBeInTheDocument();
    expect(screen.getAllByText((_, el) => el?.tagName === "P" && el.textContent === partsText(customRuleLatency()))).toHaveLength(1);
  });

  it("links Search cadence only to a reader of read:search_cadence", async () => {
    render(await NewCustomRulePage());
    expect(screen.queryByRole("link", { name: "Search cadence" })).toBeNull();
    cleanup();
    vi.spyOn(api, "caller").mockResolvedValue({
      ...writer,
      effective_permissions: [...writer.effective_permissions, "read:search_cadence"],
    });
    render(await NewCustomRulePage());
    expect(screen.getByRole("link", { name: "Search cadence" })).toHaveAttribute(
      "href",
      "/admin/search-cadence",
    );
  });

  it("hides the editor from a reader and names both granting roles", async () => {
    vi.spyOn(api, "caller").mockResolvedValue(reader);
    render(await NewCustomRulePage());
    expect(screen.queryByLabelText("custom rule editor")).toBeNull();
    expect(screen.getByText("custom_rule:write").tagName).toBe("CODE");
    expect(screen.getByText("detection_engineer").tagName).toBe("CODE");
  });

  it("hides the editor when permissions cannot be read", async () => {
    vi.spyOn(api, "caller").mockRejectedValue(new Error("down"));
    render(await NewCustomRulePage());
    expect(screen.queryByLabelText("custom rule editor")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("Permissions could not be read");
  });

  it("renders read forbidden separately from a load failure", async () => {
    vi.spyOn(api, "customRuleCatalog").mockRejectedValue(new ApiError(403, "Forbidden"));
    render(await NewCustomRulePage());
    expect(screen.getByRole("alert")).toHaveTextContent("read:custom_rules");
  });

  it("says the metric catalog failed and what to do", async () => {
    vi.spyOn(api, "customRuleCatalog").mockRejectedValue(new ApiError(500, "secret"));
    render(await NewCustomRulePage());
    expect(screen.getByRole("alert")).toHaveTextContent("catalog of metrics and bounds is unavailable");
    expect(screen.queryByText("secret")).toBeNull();
  });
});
