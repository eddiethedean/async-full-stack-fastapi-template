import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react-swc"
import { defineConfig, loadEnv } from "vite"

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, import.meta.dirname)
  const projectName =
    env.VITE_PROJECT_NAME?.trim() || "Async Full Stack FastAPI"
  const htmlProjectName = projectName
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")

  return {
    build: {
      outDir: "../backend/app/frontend",
      emptyOutDir: true,
    },
    define: {
      __PROJECT_NAME__: JSON.stringify(projectName),
    },
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "./src"),
      },
    },
    plugins: [
      {
        name: "project-title",
        transformIndexHtml: (html) =>
          html.replace("__PROJECT_NAME__", () => htmlProjectName),
      },
      tanstackRouter({
        target: "react",
        autoCodeSplitting: true,
      }),
      react(),
      tailwindcss(),
    ],
  }
})
