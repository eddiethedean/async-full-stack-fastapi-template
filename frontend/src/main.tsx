import { QueryClientProvider } from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import { type InternalAxiosRequestConfig, isAxiosError } from "axios"
import { StrictMode } from "react"
import ReactDOM from "react-dom/client"
import { client } from "./client/client.gen"
import { ThemeProvider } from "./components/theme-provider"
import { Toaster } from "./components/ui/sonner"
import {
  accessTokenKey,
  getSession,
  queryClient,
  replaceAccessToken,
  syncSessionFromStorage,
} from "./lib/auth"
import "./index.css"
import { routeTree } from "./routeTree.gen"

client.setConfig({
  baseURL: import.meta.env.VITE_API_URL ?? "",
  auth: () => getSession().accessToken ?? "",
})

const router = createRouter({ routeTree })
const authenticatedRequests = new WeakMap<InternalAxiosRequestConfig, number>()

function syncSession() {
  if (syncSessionFromStorage()) void router.invalidate()
}

client.instance.interceptors.request.use(
  (config) => {
    const { accessToken, generation } = getSession()
    if (
      accessToken &&
      config.headers.get("Authorization") === `Bearer ${accessToken}`
    ) {
      authenticatedRequests.set(config, generation)
    }
    return config
  },
  undefined,
  { synchronous: true },
)

client.instance.interceptors.response.use(undefined, (error: unknown) => {
  if (isAxiosError(error) && error.response?.status === 401 && error.config) {
    const generation = authenticatedRequests.get(error.config)
    if (generation !== undefined) {
      if (replaceAccessToken(null, generation)) {
        void router.navigate({ to: "/login", replace: true })
      } else {
        syncSession()
      }
    }
  }
  return Promise.reject(error)
})

window.addEventListener("storage", (event) => {
  if (
    event.storageArea !== localStorage ||
    (event.key !== accessTokenKey && event.key !== null)
  )
    return
  syncSession()
})

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
