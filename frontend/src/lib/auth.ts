import { QueryClient, queryOptions } from "@tanstack/react-query"
import { useSyncExternalStore } from "react"

import { UsersService } from "@/client"

const accessTokenKey = "access_token"
let session = {
  accessToken: localStorage.getItem(accessTokenKey),
  generation: 0,
}
const listeners = new Set<() => void>()

export const queryClient = new QueryClient()

export const getSession = () => session

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export const useSession = () => useSyncExternalStore(subscribe, getSession)

export function replaceAccessToken(accessToken: string | null) {
  if (accessToken === session.accessToken) return

  if (accessToken === null) {
    localStorage.removeItem(accessTokenKey)
  } else {
    localStorage.setItem(accessTokenKey, accessToken)
  }

  session = { accessToken, generation: session.generation + 1 }
  void queryClient.cancelQueries()
  queryClient.clear()
  for (const listener of listeners) listener()
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
