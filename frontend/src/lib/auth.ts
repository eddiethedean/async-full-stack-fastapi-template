import { QueryClient, queryOptions } from "@tanstack/react-query"
import { isAxiosError } from "axios"
import { useSyncExternalStore } from "react"

import { AuthService, type BrowserSession, UsersService } from "@/client"

let session = {
  browser: null as BrowserSession | null,
  restored: false,
  signingOut: false,
  generation: 0,
}
const listeners = new Set<() => void>()
export const sessionChangeKey = "asf-session-change"

function announceSessionChange() {
  const change = crypto.getRandomValues(new Uint32Array(4)).join("-")
  localStorage.setItem(sessionChangeKey, change)
}

export function withBrowserSessionLock<T>(
  operation: (canClearCookie: boolean) => Promise<T>,
): Promise<T> {
  if (navigator.locks) {
    return navigator.locks.request("asf-browser-session", () => operation(true))
  }
  return operation(false)
}

export const queryClient = new QueryClient()

export const getSession = () => session

export function subscribeSession(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export const useSession = () =>
  useSyncExternalStore(subscribeSession, getSession)

function updateSession(browser: BrowserSession | null, restored: boolean) {
  session = {
    browser,
    restored,
    signingOut: false,
    generation: session.generation + 1,
  }
  void queryClient.cancelQueries()
  queryClient.clear()
  if (browser) {
    queryClient.setQueryData(
      privateQueryKey(session.generation, "currentUser"),
      browser.user,
    )
  }
  for (const listener of listeners) listener()
}

export function replaceBrowserSession(
  browser: BrowserSession | null,
  announce = false,
) {
  updateSession(browser, true)
  if (announce) announceSessionChange()
}

export function resetBrowserSession(announce = false) {
  updateSession(null, false)
  if (announce) announceSessionChange()
}

export function setSigningOut(signingOut: boolean) {
  session = { ...session, signingOut }
  for (const listener of listeners) listener()
}

let restoring: { generation: number; promise: Promise<void> } | undefined

export async function restoreBrowserSession() {
  if (session.restored) return
  const { generation } = session
  if (restoring?.generation !== generation) {
    restoring = {
      generation,
      promise: AuthService.readBrowserSession()
        .then(({ data }) => {
          if (getSession().generation === generation)
            replaceBrowserSession(data)
        })
        .catch((error: unknown) => {
          if (getSession().generation !== generation) return
          if (isAxiosError(error) && error.response?.status === 401) {
            replaceBrowserSession(null)
            return
          }
          throw error
        })
        .finally(() => {
          if (restoring?.generation === generation) restoring = undefined
        }),
    }
  }
  await restoring.promise
}

export const privateQueryKey = (generation: number, resource?: string) =>
  resource
    ? (["private", generation, resource] as const)
    : (["private", generation] as const)

export const currentUserOptions = (generation: number) =>
  queryOptions({
    queryKey: privateQueryKey(generation, "currentUser"),
    queryFn: async ({ signal }) =>
      (await UsersService.readUserMe({ signal })).data,
    retry: false,
    staleTime: 0,
  })
