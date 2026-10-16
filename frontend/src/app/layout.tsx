import "./globals.css";
import type { Metadata } from "next";

import { AppFrame } from "@/components/auth/AppFrame";
import { SessionProvider } from "@/components/auth/session";
import { DataAgeProvider } from "@/components/shell/AutoRefresh";

export const metadata: Metadata = {
  title: {
    default: "Behavior Analytics",
    template: "%s · Behavior Analytics",
  },
  description:
    "Behavioral anomaly detection and investigation for on-premises QRadar: " +
    "seasonal volume baselines, an explicit anomaly lifecycle, and bounded " +
    "evidence answering what changed during an anomalous interval.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <DataAgeProvider>
          <SessionProvider>
            <AppFrame>{children}</AppFrame>
          </SessionProvider>
        </DataAgeProvider>
      </body>
    </html>
  );
}
