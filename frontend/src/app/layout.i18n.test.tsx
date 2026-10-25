import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/behavior",
}));

import RootLayout from "./layout";
import { setTestLocale } from "../../vitest.setup";

describe("localized root layout", () => {
  it("sets the Turkish document language", () => {
    setTestLocale("tr");
    const html = renderToStaticMarkup(<RootLayout><p>İçerik</p></RootLayout>);
    expect(html).toContain('<html lang="tr">');
  });
});
