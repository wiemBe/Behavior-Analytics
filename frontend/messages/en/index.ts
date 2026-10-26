// The English catalog, assembled from one file per namespace. English is the source of truth:
// `Messages` (src/global.d.ts) is `typeof` this module, so a wrong key fails `npm run typecheck`.
//
// Rule for every later wave:
//   - a new namespace is two new files (`messages/en/<name>.json` and `messages/tr/<name>.json`)
//     and ONE new import account from "./account.json";
import account from "./account.json";
import alerts from "./alerts.json";
import auth from "./auth.json";
import common from "./common.json";
import customRules from "./customRules.json";
import dashboard from "./dashboard.json";
import dashboardBoundary from "./dashboardBoundary.json";
import enums from "./enums.json";
import errors from "./errors.json";
import format from "./format.json";
import home from "./home.json";
import login from "./login.json";
import meta from "./meta.json";
import offenses from "./offenses.json";
import password from "./password.json";
import pwchart from "./pwchart.json";
import rules from "./rules.json";
import shell from "./shell.json";
import states from "./states.json";
import trend from "./trend.json";
import ui from "./ui.json";
import units from "./units.json";
import widgets from "./widgets.json";

const messages = {
  account,
  alerts,
  auth,
  common,
  customRules,
  dashboard,
  dashboardBoundary,
  enums,
  errors,
  format,
  home,
  login,
  meta,
  offenses,
  password,
  pwchart,
  rules,
  shell,
  states,
  trend,
  ui,
  units,
  widgets,
};

export default messages;
