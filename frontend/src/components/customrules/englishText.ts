// English fallback for pure custom-rule helpers. Converted route code passes its translator;
// existing callers and tests keep the original English output without importing the full catalog.

import customRules from "../../../messages/en/customRules.json";
import { createTextTranslator } from "@/i18n/textTranslator";

export const customRulesEnglishText = createTextTranslator("en", { customRules });
