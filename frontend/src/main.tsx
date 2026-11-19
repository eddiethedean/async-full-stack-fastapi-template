import { QueryClientProvider } from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import { type InternalAxiosRequestConfig, isAxiosError } from "axios"
import { StrictMode } from "react"
import ReactDOM from "react-dom/client"
import { client } from "./client/client.gen"
import { ThemeProvider } from "./components/theme-provider"
import { Toaster } from "./components/ui/sonner"
import {
  getSession,
  queryClient,
  replaceBrowserSession,
  resetBrowserSession,
  sessionChangeKey,
  subscribeSession,
} from "./lib/auth"
import "./index.css"
import { routeTree } from "./routeTree.gen"

client.setConfig({
  baseURL: import.meta.env.VITE_API_URL ?? "",
  withCredentials: true,
})

const router = createRouter({ routeTree })
const authenticatedRequests = new WeakMap<InternalAxiosRequestConfig, number>()

function refreshBrowserSession() {
  resetBrowserSession()
  void router.invalidate()
}

client.instance.interceptors.request.use(
  (config) => {
    const { browser, generation } = getSession()
    if (config.headers.has("Authorization")) return config
    config.headers.set("X-Browser-Session", browser?.session_id ?? "unrestored")
    if (browser) {
      authenticatedRequests.set(config, generation)
      if (
        !["GET", "HEAD", "OPTIONS"].includes(
          config.method?.toUpperCase() ?? "GET",
        )
      ) {
        config.headers.set("X-CSRF-Token", browser.csrf_token)
      }
    }
    return config
  },
  undefined,
  { synchronous: true },
)

client.instance.interceptors.response.use(undefined, (error: unknown) => {
  if (
    isAxiosError(error) &&
    error.response?.status === 401 &&
    error.config &&
    authenticatedRequests.get(error.config) === getSession().generation
  ) {
    refreshBrowserSession()
  }
  return Promise.reject(error)
})

window.addEventListener("storage", (event) => {
  if (event.storageArea === localStorage && event.key === sessionChangeKey) {
    refreshBrowserSession()
  }
})

let expiryTimer: number | undefined
function watchSessionExpiry() {
  window.clearTimeout(expiryTimer)
  const { browser } = getSession()
  if (!browser) return
  const remaining = Date.parse(browser.expires_at) - Date.now()
  if (!Number.isFinite(remaining) || remaining <= 0) {
    replaceBrowserSession(null)
    void router.navigate({ to: "/login", replace: true })
  } else {
    expiryTimer = window.setTimeout(
      watchSessionExpiry,
      Math.min(remaining, 2147483647),
    )
  }
}
subscribeSession(watchSessionExpiry)

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider defaultTheme="dark" storageKey="vite-ui-theme">
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
        <Toaster richColors closeButton />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
