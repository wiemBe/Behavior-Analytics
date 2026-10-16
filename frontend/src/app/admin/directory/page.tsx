import type { Metadata } from "next";

import { DirectoryPage } from "./DirectoryPage";

export const metadata: Metadata = { title: "Directory" };

export default function Page() {
  return <DirectoryPage />;
}
