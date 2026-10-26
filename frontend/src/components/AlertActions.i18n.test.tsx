import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../vitest.setup";
import { AlertActions } from "./AlertActions";
import { ApiError, api } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

beforeEach(() => setTestLocale("tr"));

describe("alert actions in Turkish", () => {
  it("uses the shared localized refusal for a 403", async () => {
    vi.spyOn(api, "acknowledgeAlert").mockRejectedValue(new ApiError(403, "secret"));
    render(<AlertActions alertId="a-1" status="OPEN" />);
    await userEvent.click(screen.getByRole("button", { name: "Alındı olarak işaretle" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bu işlemi yapma izniniz yok.");
  });

  it("confirms resolve and explains a 409 without leaking the server text", async () => {
    vi.spyOn(api, "resolveAlert").mockRejectedValue(new ApiError(409, "secret state"));
    render(<AlertActions alertId="a-1" status="ACKNOWLEDGED" />);
    await userEvent.click(screen.getByRole("button", { name: "Çöz" }));
    expect(screen.getByText(/Çözülen bir uyarı yeniden açılamaz/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Uyarıyı çöz" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("geçerli durumla çelişiyor");
    expect(alert).not.toHaveTextContent("secret state");
  });
});
