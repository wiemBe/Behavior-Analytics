// Session cookie, the return path, and which 401s leave the page.

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  credentialMode,
  destinationAfterLogin,
  destinationAfterPasswordChange,
  loginRedirectFor,
  request,
  safeReturnPath,
  sessionNavigation,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("credentialMode", () => {
  it("includes the cookie when the API is on another port", () => {
    expect(credentialMode("http://localhost:3000/login", "http://localhost:8000/api/v1")).toBe(
      "include",
    );
  });

  it("stays same-origin for a relative API base", () => {
    expect(credentialMode("http://127.0.0.1:3102/login", "/api/v1")).toBe("same-origin");
  });

  it("stays same-origin when the page and the API share an origin", () => {
    expect(credentialMode("https://soc.example/login", "https://soc.example/api/v1")).toBe(
      "same-origin",
    );
  });
});

describe("safeReturnPath", () => {
  it("keeps a same-origin path and its query", () => {
    expect(safeReturnPath("/behavior/waf?source=1")).toBe("/behavior/waf?source=1");
  });

  it.each([
    null,
    "",
    "   ",
    "https://evil.example/phish",
    "//evil.example",
    "/\\evil.example",
    "/foo\\bar",
    "/foo://bar",
    "/%2F%2Fevil.example",
    "/%09/behavior",
    "/login",
    "/login?next=/behavior",
    "/login/help",
  ])("refuses %j", (raw) => {
    expect(safeReturnPath(raw)).toBe("/");
  });
});

describe("loginRedirectFor", () => {
  it("sends a missing session to login with the return path", () => {
    expect(loginRedirectFor(401, "/me", "GET", "Authentication required.", "/alerts")).toBe(
      "/login?next=%2Falerts",
    );
  });

  it("leaves a wrong password on the sign-in page", () => {
    expect(loginRedirectFor(401, "/auth/login", "POST", "Invalid username or password", "/login")).toBe(
      null,
    );
  });

  it("leaves a wrong current password on the password page", () => {
    expect(
      loginRedirectFor(401, "/auth/password", "POST", "current password is wrong", "/account/password"),
    ).toBe(null);
  });

  it("does not redirect a page that is already sign-in", () => {
    expect(loginRedirectFor(401, "/users", "GET", "Authentication required.", "/login?next=%2F")).toBe(
      null,
    );
  });

  it("ignores other statuses", () => {
    expect(loginRedirectFor(403, "/users", "GET", "forbidden", "/admin/users")).toBe(null);
  });
});

describe("destinations", () => {
  it("returns to the safe path after sign-in", () => {
    expect(destinationAfterLogin("/alerts", false)).toBe("/alerts");
    expect(destinationAfterLogin("https://evil.example", false)).toBe("/");
  });

  it("sends a password that must change to that page and keeps the return path", () => {
    expect(destinationAfterLogin("/alerts", true)).toBe("/account/password?next=%2Falerts");
    expect(destinationAfterLogin(null, true)).toBe("/account/password");
  });

  it("lands a finished password change on the return path, not the form", () => {
    expect(destinationAfterPasswordChange("/alerts", "/account/password")).toBe("/alerts");
    expect(destinationAfterPasswordChange(null, "/account/password")).toBe("/");
    expect(destinationAfterPasswordChange("", "/behavior")).toBe("/behavior");
  });
});

describe("request", () => {
  it("sends the cookie and stores no token", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ subject: "ada" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    await request("/me");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/me"),
      expect.objectContaining({ credentials: "include", cache: "no-store" }),
    );
    expect(setItem).not.toHaveBeenCalled();
  });

  it("redirects a 401 and still throws", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "Authentication required." }),
      }),
    );
    window.history.pushState({}, "", "/behavior/waf");
    const assign = vi.spyOn(sessionNavigation, "assign").mockImplementation(() => {});
    const error = await request("/me").then(
      () => null,
      (err: unknown) => err,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(assign).toHaveBeenCalledWith("/login?next=%2Fbehavior%2Fwaf");
  });

  it("does not redirect a refused sign-in or a wrong current password", async () => {
    const assign = vi.spyOn(sessionNavigation, "assign").mockImplementation(() => {});
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "Invalid username or password" }),
      }),
    );
    await expect(request("/auth/login", { method: "POST" })).rejects.toBeInstanceOf(ApiError);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "current password is wrong" }),
      }),
    );
    await expect(request("/auth/password", { method: "POST" })).rejects.toBeInstanceOf(ApiError);
    expect(assign).not.toHaveBeenCalled();
  });

  it("keeps a 422 list on the error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        statusText: "Unprocessable",
        json: async () => ({ detail: ["too short", "matches the username"] }),
      }),
    );
    const error = await request("/auth/password", { method: "POST" }).then(
      () => null,
      (err: unknown) => err,
    );
    expect(error).toMatchObject({
      status: 422,
      detail: ["too short", "matches the username"],
    });
  });
});
