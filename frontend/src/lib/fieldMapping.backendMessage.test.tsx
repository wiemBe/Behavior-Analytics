import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useBackendMessage } from "@/i18n/backendMessage";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { setTestLocale } from "../../vitest.setup";

import { collectionState, type ProfileStateInput } from "./fieldMapping";

function State({ profile }: { profile: ProfileStateInput }) {
  const i18n = useBehaviorI18n();
  const backendMessage = useBackendMessage();
  return (
    <p>
      {
        collectionState(
          { ...profile, renderBackendMessage: backendMessage },
          i18n,
        ).label
      }
    </p>
  );
}

const base: ProfileStateInput = {
  enabled: true,
  profile_version: 1,
  validation_status: "VALID",
  validated_profile_version: 1,
  collection_eligible: false,
  ineligible_reason: "the profile is disabled",
};

describe("coded collection ineligibility", () => {
  it.each([
    [
      "WAF collection is disabled (WAF_COLLECTION_ENABLED=false)",
      "validation.waf.ineligible.collection_disabled",
      {},
      "Duraklatıldı — WAF toplaması devre dışı (WAF_COLLECTION_ENABLED=false)",
    ],
    [
      "the profile's log source no longer exists",
      "validation.waf.ineligible.source_missing",
      {},
      "Duraklatıldı — Profilin log source'u artık mevcut değil",
    ],
    [
      "the profile is disabled",
      "validation.profile.ineligible.disabled",
      {},
      "Duraklatıldı — Profil devre dışı",
    ],
    [
      "monitoring is disabled for the log source",
      "validation.waf.ineligible.monitoring_disabled",
      {},
      "Duraklatıldı — Log source için izleme devre dışı",
    ],
    [
      "profile version 1 has never been validated",
      "validation.profile.ineligible.version_never_validated",
      { profile_version: 1 },
      "Duraklatıldı — Profil sürümü 1 hiç doğrulanmadı",
    ],
    [
      "profile version 12000 has not been validated (last validated: 9999)",
      "validation.profile.ineligible.version_not_validated",
      { profile_version: 12000, validated_profile_version: 9999 },
      "Duraklatıldı — Profil sürümü 12.000 doğrulanmadı (son doğrulanan: 9.999)",
    ],
    [
      "profile validation is INVALID",
      "validation.profile.ineligible.status",
      { validation_status: "INVALID" },
      "Duraklatıldı — Profil doğrulama durumu: Geçersiz",
    ],
  ])("renders %s inside the Turkish paused frame", (text, code, params, expected) => {
    setTestLocale("tr");
    render(
      <State
        profile={{
          ...base,
          ineligible_reason: text,
          ineligible_reason_code: code,
          ineligible_reason_params: params,
        }}
      />,
    );
    expect(screen.getByText(expected)).toBeInTheDocument();
  });

  it("keeps the exact English frame and reason even when a code is present", () => {
    setTestLocale("en");
    render(
      <State
        profile={{
          ...base,
          ineligible_reason_code: "validation.profile.ineligible.disabled",
          ineligible_reason_params: {},
        }}
      />,
    );
    expect(screen.getByText("Paused — the profile is disabled")).toBeInTheDocument();
  });

  it("falls back to the API text for an unknown code", () => {
    setTestLocale("tr");
    render(
      <State
        profile={{
          ...base,
          ineligible_reason: "future reason",
          ineligible_reason_code: "validation.future.ineligible",
          ineligible_reason_params: {},
        }}
      />,
    );
    expect(screen.getByText("Duraklatıldı — future reason")).toBeInTheDocument();
  });
});
