import createNextIntlPlugin from "next-intl/plugin";

// Interface language without locale routing: the locale comes from the `ba_locale` cookie
// (src/i18n/request.ts), never from the URL.
const withNextIntl = createNextIntlPlugin();

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // The QRadar SEC token lives only in the backend. The browser talks to the
  // backend API and never to QRadar or the MCP service directly.
  env: {
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  },
};

export default withNextIntl(nextConfig);
