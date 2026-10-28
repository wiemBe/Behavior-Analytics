// The English of the sign-in and account area for its pure functions' default translator
// (`errorTexts`, `accountStatusLines`, the probe copy): a caller without a translator keeps today's
// English. It holds only the namespaces this area reads (`auth`, `common`), so it adds nothing
// else to the chunk; route code passes its own translator.

import auth from "../../../messages/en/auth.json";
import common from "../../../messages/en/common.json";
import { createTextTranslator } from "@/i18n/textTranslator";

export const englishText = createTextTranslator("en", { auth, common });
