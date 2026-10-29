// English fallback for pure rule-statistics helpers. Converted route code passes its translator;
// existing callers and tests keep the original English output without importing the full catalog.

import rules from "../../../messages/en/rules.json";
import { createTextTranslator } from "@/i18n/textTranslator";

export const rulesEnglishText = createTextTranslator("en", { rules });
