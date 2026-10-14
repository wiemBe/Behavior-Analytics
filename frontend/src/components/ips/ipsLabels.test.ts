import { describe, expect, it } from "vitest";

import { ipsListItem, portEntity } from "./fixtures";
import {
  dimensionNoun,
  entityLabel,
  IPS_TYPES,
  ipsSubjectText,
  ipsTypeLabel,
  isIpsRow,
  isIpsType,
  rowTypeLabel,
} from "./ipsLabels";

describe("ipsTypeLabel", () => {
  it.each([
    ["IPS_PORT_RATE_SPIKE", "Destination-port rate spike"],
    ["IPS_DESTINATION_RATE_SPIKE", "Destination rate spike"],
    ["IPS_SIGNATURE_BURST", "Signature burst"],
    ["IPS_NEW_DESTINATION_PORT", "New destination port"],
    ["IPS_SOURCE_CONCENTRATION_SPIKE", "Source concentration spike"],
    ["IPS_PORT_FANOUT_SPIKE", "Port fan-out spike"],
    ["IPS_BLOCK_RATIO_SHIFT", "Block-ratio shift"],
  ])("names %s", (type, label) => {
    expect(ipsTypeLabel(type)).toBe(label);
    expect(isIpsType(type)).toBe(true);
  });

  it("covers exactly the seven detector types", () => {
    expect(IPS_TYPES).toHaveLength(7);
  });

  it("shows an unknown type as sent, never with a Phase A label", () => {
    expect(ipsTypeLabel("IPS_FUTURE_THING")).toBe("IPS_FUTURE_THING");
    expect(isIpsType("IPS_FUTURE_THING")).toBe(false);
    expect(isIpsType("VOLUME_SPIKE")).toBe(false);
    // A Phase A type on an IPS row must not borrow the Phase A name.
    expect(rowTypeLabel({ subject_kind: "IPS_ENTITY", anomaly_type: "VOLUME_SPIKE" })).toBe(
      "VOLUME_SPIKE",
    );
    expect(rowTypeLabel({ subject_kind: "LOG_SOURCE", anomaly_type: "VOLUME_SPIKE" })).not.toBe(
      "VOLUME_SPIKE",
    );
  });
});

describe("entityLabel", () => {
  it("names a destination port and a destination address", () => {
    expect(entityLabel(portEntity())).toBe("Destination port 445/tcp");
    expect(
      entityLabel({ dimension: "DESTINATION_IP", value_key: "10.0.4.12", display_name: null }),
    ).toBe("Destination 10.0.4.12");
  });

  it("names a signature by its display name with its QID, else by QID", () => {
    expect(
      entityLabel({ dimension: "SIGNATURE", value_key: "qid:5000123", display_name: "SMB probe" }),
    ).toBe("SMB probe (QID 5000123)");
    expect(
      entityLabel({ dimension: "SIGNATURE", value_key: "qid:5000123", display_name: null }),
    ).toBe("Signature QID 5000123");
  });

  it("shows an unknown dimension as sent", () => {
    expect(dimensionNoun("SOURCE_IP")).toBe("SOURCE_IP");
    expect(entityLabel({ dimension: "SOURCE_IP", value_key: "k", display_name: null })).toBe(
      "SOURCE_IP k",
    );
  });
});

describe("ipsSubjectText and isIpsRow", () => {
  it("names the entity, or Source-level", () => {
    expect(ipsSubjectText(ipsListItem())).toBe("Destination port 445/tcp");
    expect(ipsSubjectText(ipsListItem({ subject_kind: "IPS_SOURCE", ips_entity: null }))).toBe(
      "Source-level",
    );
  });

  it("does not borrow the source's name when the entity is missing", () => {
    expect(ipsSubjectText(ipsListItem({ ips_entity: null }))).toBe("Entity not recorded");
  });

  it("recognises IPS rows by subject kind", () => {
    expect(isIpsRow({ subject_kind: "IPS_ENTITY" })).toBe(true);
    expect(isIpsRow({ subject_kind: "IPS_SOURCE" })).toBe(true);
    expect(isIpsRow({ subject_kind: "WAF_ENDPOINT" })).toBe(false);
    expect(isIpsRow({ subject_kind: undefined })).toBe(false);
  });
});
