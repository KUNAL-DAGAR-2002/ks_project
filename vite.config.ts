import vinext from "vinext";
import { defineConfig } from "vite";

// Render builds directly from the Git repository, so this configuration must
// not depend on Codex/Sites-only helper files that are excluded from Git.
export default defineConfig({
  plugins: [vinext()],
  server:
    process.env.CODEX_SANDBOX === "seatbelt"
      ? { watch: { useFsEvents: false, usePolling: true } }
      : undefined,
});
