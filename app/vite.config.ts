import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tauri expects a fixed port and must not have Rust errors hidden by Vite's screen clearing.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**"] },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    env: { VITE_ENGINE_URL: "http://127.0.0.1:9999", VITE_ENGINE_TOKEN: "test-token" },
  },
});
