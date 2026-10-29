// The per-route provider (I18N-12b). A page hands its content to this component, which gives the
// browser the shell plus the route's namespaces (`routeNamespaces.ts`) instead of the whole catalog.
// The nested provider replaces the root one; the shell values are the same objects as the root's,
// which the server renders once (measured: no repeated shell in the HTML).
import { NextIntlClientProvider, useLocale, useMessages } from "next-intl";
import type { ReactElement, ReactNode } from "react";

import { clientMessages } from "./clientMessages";
import { ROUTE_NAMESPACES, routeMessages } from "./routeNamespaces";

export function RouteMessages({ route, children }: { route: string; children: ReactNode }) {
  const locale = useLocale();
  const messages = routeMessages(route, clientMessages(useMessages()));
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      {children}
    </NextIntlClientProvider>
  );
}

/**
 * A page component whose content is rendered inside the route's provider. `route` is the page's
 * path under `src/app` (`/anomalies/[id]`); it must be a key of `ROUTE_NAMESPACES`.
 *
 * An async page is awaited here. A synchronous page may call the server hooks (`useTranslations`),
 * which only work while React renders it, so it stays a child element and is not called here.
 */
export function withRouteMessages<Args extends [] | [object]>(
  route: string,
  Page: (...args: Args) => Promise<ReactNode>,
): (...args: Args) => Promise<ReactElement>;
export function withRouteMessages<Args extends [] | [object]>(
  route: string,
  Page: (...args: Args) => ReactNode,
): (...args: Args) => ReactElement;
export function withRouteMessages<Args extends [] | [object]>(
  route: string,
  Page: (...args: Args) => ReactNode | Promise<ReactNode>,
) {
  if (!Object.hasOwn(ROUTE_NAMESPACES, route)) throw new Error(`No message set for route ${route}`);
  if (Page.constructor.name === "AsyncFunction") {
    return async function RoutePage(...args: Args) {
      const content = await Page(...args);
      return <RouteMessages route={route}>{content}</RouteMessages>;
    };
  }
  const Sync = Page as (props: object) => ReactNode;
  return function RoutePage(...args: Args) {
    return (
      <RouteMessages route={route}>
        <Sync {...(args[0] ?? {})} />
      </RouteMessages>
    );
  };
}
