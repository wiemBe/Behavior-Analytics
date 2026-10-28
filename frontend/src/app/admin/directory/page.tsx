import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { DirectoryPage } from "./DirectoryPage";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("directory.meta");
  return { title: t("title") };
}

export default function Page() {
  return <DirectoryPage />;
}
