import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { QRadarField } from "@/lib/api";

import { FieldValue } from "./FieldValue";

const shown = (field: QRadarField<number>) =>
  render(<FieldValue field={field}>{(value) => <span data-testid="value">{value}</span>}</FieldValue>);

describe("FieldValue", () => {
  it("shows the value of a field that was read", () => {
    shown({ state: "OK", value: 182, reason_code: null, reason: null });
    expect(screen.getByTestId("value")).toHaveTextContent("182");
  });

  it("shows a measured zero as 0", () => {
    shown({ state: "OK", value: 0, reason_code: null, reason: null });
    expect(screen.getByTestId("value")).toHaveTextContent("0");
  });

  it.each([
    ["NOT_COLLECTED", "Not collected"],
    ["FORBIDDEN", "Forbidden"],
    ["FAILED", "Failed"],
  ] as const)("shows %s as its state and its reason, with no value", (state, label) => {
    shown({ state, value: null, reason_code: "timeout", reason: "QRadar did not answer within the read timeout." });
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText("QRadar did not answer within the read timeout.")).toBeInTheDocument();
    expect(screen.queryByTestId("value")).not.toBeInTheDocument();
  });

  it("never shows a number for a field that was not read, even when one arrives with it", () => {
    shown({ state: "FAILED", value: 7, reason_code: "timeout", reason: "It timed out." });
    expect(screen.queryByTestId("value")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("7");
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("does not call an OK field with no value healthy: it says it was not collected", () => {
    shown({ state: "OK", value: null, reason_code: null, reason: null });
    expect(screen.queryByTestId("value")).not.toBeInTheDocument();
    expect(screen.getByText("Not collected")).toBeInTheDocument();
    expect(screen.getByText("The backend sent no value.")).toBeInTheDocument();
  });
});
