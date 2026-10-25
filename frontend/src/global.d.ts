import type en from "../messages/en";

// Typed message keys: a wrong key fails `npm run typecheck`. English is the source of truth.
declare module "next-intl" {
  interface AppConfig {
    Locale: "en" | "tr";
    Messages: typeof en;
  }
}
