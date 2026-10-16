import { describe, expect, it } from "vitest";

import { bindOutcome, probeHeadline, tlsOutcome, trustWords } from "./probeCopy";

describe("directory test words", () => {
  it("names a TLS failure and keeps the backend reason", () => {
    const word = tlsOutcome("failed", "certificate verify failed");
    expect(word).toEqual({ label: "Failed", tone: "crit", detail: "certificate verify failed" });
    expect(probeHeadline("failed", "not_attempted", "ada")).toBe("Connection failed at TLS.");
  });

  it("names a successful bind with the account", () => {
    expect(tlsOutcome("ok", null)).toEqual({ label: "OK", tone: "ok", detail: null });
    expect(bindOutcome("ok", null).label).toBe("OK");
    expect(probeHeadline("ok", "ok", "ada")).toBe("Connected and bound as ada.");
  });

  it("names a bind that was not attempted, and a refused bind", () => {
    expect(bindOutcome("not_attempted", "TLS failed")).toEqual({
      label: "Not attempted",
      tone: "neutral",
      detail: "TLS failed",
    });
    expect(bindOutcome("invalid_credentials", "invalid credentials").label).toBe("Refused");
    expect(probeHeadline("ok", "invalid_credentials", "ada")).toBe("TLS succeeded. The bind did not.");
  });

  it("names the trust store in words", () => {
    expect(trustWords("system")).toBe("System trust store");
    expect(trustWords("ca_pem")).toBe("Uploaded CA certificate");
  });
});
