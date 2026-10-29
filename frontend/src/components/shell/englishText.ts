// The English of the connection status for its default translator (`connectionSignals` called
// without one keeps today's English). It holds only the namespaces ConnectionStatus reads
// (`common`, `format`, `shell`); the component itself passes the translator from the provider.

import common from "../../../messages/en/common.json";
import format from "../../../messages/en/format.json";
import shell from "../../../messages/en/shell.json";
import { createTextTranslator } from "@/i18n/textTranslator";

export const englishText = createTextTranslator("en", { common, format, shell });
