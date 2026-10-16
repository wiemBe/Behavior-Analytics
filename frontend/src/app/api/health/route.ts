import pkg from "../../../../package.json";

// Container health probe target for docker-compose. `version` is the frontend's own build:
// `NEXT_PUBLIC_APP_VERSION` when the image build set it, otherwise package.json's version.
export function GET() {
  return Response.json({
    status: "ok",
    version: process.env.NEXT_PUBLIC_APP_VERSION || pkg.version,
  });
}
