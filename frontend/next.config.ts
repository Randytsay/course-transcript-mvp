import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    const api = process.env.API_INTERNAL_BASE_URL;
    return api ? [{ source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }] : [];
  }
};

export default nextConfig;
