import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    // Kirana bills and handwritten sales sheets are commonly high-resolution
    // phone photos. The default 1 MB limit rejects them before our API proxy
    // route can forward the upload to FastAPI.
    serverActions: {
      bodySizeLimit: "20mb",
    },
  },
};

export default nextConfig;
