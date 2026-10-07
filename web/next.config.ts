import type { NextConfig } from "next";

// The browser calls /api/*, and Next forwards those requests to FastAPI.
// This avoids CORS setup and keeps the API address in one place.
const API_URL = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
