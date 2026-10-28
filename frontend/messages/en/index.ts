// The English catalog, assembled from one file per namespace. English is the source of truth:
// `Messages` (src/global.d.ts) is `typeof` this module, so a wrong key fails `npm run typecheck`.
//
// Rule for every later wave:
//   - a new namespace is two new files (`messages/en/<name>.json` and `messages/tr/<name>.json`)
//     and ONE new import account from "./account.json";
import account from "./account.json";
import admin from "./admin.json";
import alerts from "./alerts.json";
import anomalies from "./anomalies.json";
import auth from "./auth.json";
import backend from "./backend.json";
import behavior from "./behavior.json";
import cadence from "./cadence.json";
import catalog from "./catalog.json";
import catchall from "./catchall.json";
import classification from "./classification.json";
import common from "./common.json";
import configChanges from "./configChanges.json";
import coverage from "./coverage.json";
import customRules from "./customRules.json";
import dashboard from "./dashboard.json";
import dashboardBoundary from "./dashboardBoundary.json";
import directory from "./directory.json";
import enums from "./enums.json";
import errors from "./errors.json";
import evidence from "./evidence.json";
import fieldMapping from "./fieldMapping.json";
import fleet from "./fleet.json";
import format from "./format.json";
import history from "./history.json";
import home from "./home.json";
import ips from "./ips.json";
import logSources from "./logSources.json";
import login from "./login.json";
import meta from "./meta.json";
import offenses from "./offenses.json";
import outage from "./outage.json";
import password from "./password.json";
import propertyWatch from "./propertyWatch.json";
import pwchart from "./pwchart.json";
import rules from "./rules.json";
import searches from "./searches.json";
import shell from "./shell.json";
import states from "./states.json";
import trend from "./trend.json";
import ui from "./ui.json";
import units from "./units.json";
import users from "./users.json";
import waf from "./waf.json";
import widgets from "./widgets.json";

const messages = {
  account,
  admin,
  alerts,
  anomalies,
  auth,
  backend,
  behavior,
  cadence,
  catalog,
  catchall,
  classification,
  common,
  configChanges,
  coverage,
  customRules,
  dashboard,
  dashboardBoundary,
  directory,
  enums,
  errors,
  evidence,
  fieldMapping,
  fleet,
  format,
  history,
  home,
  ips,
  logSources,
  login,
  meta,
  offenses,
  outage,
  password,
  propertyWatch,
  pwchart,
  rules,
  searches,
  shell,
  states,
  trend,
  ui,
  units,
  users,
  waf,
  widgets,
};

export default messages;
