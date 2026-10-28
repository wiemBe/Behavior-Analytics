// The English of the Dashboard area (the widget system and the Home widgets) for its pure
// functions' default translator: `dashboardAccess`, `nameProblem`, `unavailableReason`,
// `earlyState`, `secondary`, the chart builders and the rest take `t` as their last argument and
// default to this, so a caller without a translator (the existing tests, the routes not converted
// yet) keeps today's English.
//
// It holds only this area's namespaces and lives in this area's chunk. It is not a shared bundle:
// that file serves the shell, which is on every page, and must not grow with each wave. The
// catalog a component sees comes from the provider; this copy exists for the defaults only.

import dashboard from "../../messages/en/dashboard.json";
import enums from "../../messages/en/enums.json";
import home from "../../messages/en/home.json";
import units from "../../messages/en/units.json";
import widgets from "../../messages/en/widgets.json";
import { createTextTranslator } from "@/i18n/textTranslator";

export const englishText = createTextTranslator("en", { dashboard, enums, home, units, widgets });
