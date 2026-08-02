import { startProdServer } from "vinext/server/prod-server";

// Render already places the service behind its Cloudflare edge. Disabling
// Vinext's on-the-fly compression avoids truncated JS streams when the edge
// closes or replaces a compressed upstream response before it is complete.
await startProdServer({
  host: "0.0.0.0",
  port: Number(process.env.PORT || 3000),
  outDir: "dist",
  noCompression: true,
});
