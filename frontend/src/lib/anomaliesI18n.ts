// The English of the anomaly list and the anomaly detail, for the helpers only they use
// (`summarize`, `anomalyQuery`). Kept apart from `behaviorI18n` so a page that only needs the
// behavior words does not carry the anomaly catalog in its chunk.

import anomalies from "../../messages/en/anomalies.json";
import behavior from "../../messages/en/behavior.json";
import enums from "../../messages/en/enums.json";
import format from "../../messages/en/format.json";
import units from "../../messages/en/units.json";
import { createTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

export const ENGLISH_ANOMALIES: BehaviorI18n = {
  t: createTextTranslator("en", { anomalies, behavior, enums, format, units }),
  locale: "en",
};
