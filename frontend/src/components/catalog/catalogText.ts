// The English default of the catalog widgets' pure helpers (`derive.ts`): the same `FleetText`
// shape as the fleet routes', over this area's namespaces. A caller without a translator (the
// existing tests, a route that is not converted) keeps today's English; the widgets pass their own.

import { createTextTranslator } from "@/i18n/textTranslator";
import type { FleetText } from "@/lib/fleetText";

import catalog from "../../../messages/en/catalog.json";
import catchall from "../../../messages/en/catchall.json";
import coverage from "../../../messages/en/coverage.json";
import enums from "../../../messages/en/enums.json";
import errors from "../../../messages/en/errors.json";
import fleet from "../../../messages/en/fleet.json";
import units from "../../../messages/en/units.json";

export const englishCatalogText: FleetText = {
  say: createTextTranslator("en", { catalog, catchall, coverage, enums, errors, fleet, units }),
  locale: "en",
};
