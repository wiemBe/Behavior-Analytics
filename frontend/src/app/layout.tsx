import "./globals.css";
import type { Metadata } from "next";
import { NextIntlClientProvider, useLocale, useMessages } from "next-intl";
import { getTranslations } from "next-intl/server";

import { AppFrame } from "@/components/auth/AppFrame";
import { SessionProvider } from "@/components/auth/session";
import { DataAgeProvider } from "@/components/shell/AutoRefresh";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("meta");
  return {
    title: { default: t("title"), template: t("titleTemplate", { title: "%s" }) },
    description: t("description"),
  };
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const locale = useLocale();
  const messages = useMessages();
  return (
    <html lang={locale}>
      <body>
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          <DataAgeProvider>
            <SessionProvider>
              <AppFrame>{children}</AppFrame>
            </SessionProvider>
          </DataAgeProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
