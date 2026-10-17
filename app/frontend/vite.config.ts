import react from "@vitejs/plugin-react";
// defineConfig vem do vitest, e nao do vite, para que o bloco "test" seja tipado.
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Em desenvolvimento o vite faz o proxy que, em producao, o nginx faz.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
    // O teste das 20 decisoes da fila leva ~0,5 s sozinho e passava dos 5 s padrao sob
    // carga; com os findBy a 5 s (setupTests), o teto por teste precisa ser maior.
    testTimeout: 30000,
  },
});
