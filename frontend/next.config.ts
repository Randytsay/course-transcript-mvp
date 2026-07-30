import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    // Rewrites are resolved when the production image is built, not when its
    // container starts. The Docker-network service name is therefore a safe
    // build-time default; browsers only ever request the same-origin /api URL.
    const api = process.env.API_INTERNAL_BASE_URL ?? "http://api:8000";
    return [{ source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }];
  }
};

export default nextConfig;
