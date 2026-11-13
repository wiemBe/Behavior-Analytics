// The collection state of an identity bucket, in words. The colour only repeats the word.

import { useTranslations } from "next-intl";

import type { CollectionState } from "./identityFormat";

const TONE: Record<CollectionState, string> = {
  COMPLETE: "ok",
  PARTIAL: "warn",
  TRUNCATED: "warn",
  NOT_COLLECTED: "",
};

export function IdentityStateBadge({ state }: { state: CollectionState }) {
  const t = useTranslations("identity.state");
  return <span className={`pill pill-quiet ${TONE[state]}`}>{t(state)}</span>;
}
