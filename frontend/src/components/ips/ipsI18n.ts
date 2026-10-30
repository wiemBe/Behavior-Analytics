// The words and figures of the IPS area (the investigation, the sources administration and the
// Dashboard widget) for its pure helpers.
//
// A helper that builds text takes a `BehaviorI18n` as its last argument and defaults to the
// `ENGLISH_IPS` here, so every caller outside this area (`AnomalyRows`,
// `lib/home/recentAnomalies`, the tests) and every unconverted route keeps today's English.
// Route code passes its own: `useBehaviorI18n()` in a component, `getBehaviorI18n()`
// (lib/behaviorI18n.server) in a server page. Nothing here translates by matching text: a helper
// returns a code or builds its sentence from a message key.

import behavior from "../../../messages/en/behavior.json";
import common from "../../../messages/en/common.json";
import enums from "../../../messages/en/enums.json";
import fieldMapping from "../../../messages/en/fieldMapping.json";
import errors from "../../../messages/en/errors.json";
import ips from "../../../messages/en/ips.json";
import { createTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

/** The English of this area, for the helpers' defaults. */
export const ENGLISH_IPS: BehaviorI18n = {
  t: createTextTranslator("en", { behavior, common, enums, errors, fieldMapping, ips }),
  locale: "en",
};
