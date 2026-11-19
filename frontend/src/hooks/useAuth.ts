import { useMutation, useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import {
  AuthService,
  type BrowserLogin,
  type UserRegister,
  UsersService,
} from "@/client"
import {
  currentUserOptions,
  getSession,
  replaceBrowserSession,
  resetBrowserSession,
  restoreBrowserSession,
  setSigningOut,
  useSession,
  withBrowserSessionLock,
} from "@/lib/auth"
import { handleError } from "@/utils"
import useCustomToast from "./useCustomToast"

const isLoggedIn = async () => {
  await restoreBrowserSession()
  return getSession().browser !== null
}

const useAuth = () => {
  const navigate = useNavigate()
  const { showErrorToast } = useCustomToast()
  const { browser, generation } = useSession()

  const { data: user } = useQuery({
    ...currentUserOptions(generation),
    enabled: browser !== null,
  })

  const signUpMutation = useMutation({
    mutationFn: (data: UserRegister) =>
      UsersService.registerUser({ body: data }),
    onSuccess: () => {
      navigate({ to: "/login" })
    },
    onError: handleError.bind(showErrorToast),
  })

  const loginMutation = useMutation({
    mutationFn: (data: BrowserLogin) =>
      withBrowserSessionLock(async () => {
        const response = await AuthService.createBrowserSession({ body: data })
        replaceBrowserSession(response.data, true)
      }),
    onSuccess: () => {
      void navigate({ to: "/" })
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = async () => {
    if (generation !== getSession().generation || getSession().signingOut)
      return
    setSigningOut(true)
    try {
      const signedOut = await withBrowserSessionLock(async (canClearCookie) => {
        if (generation !== getSession().generation) return false
        await AuthService.deleteBrowserSession({
          query: { clear_cookie: canClearCookie },
        })
        resetBrowserSession(true)
        return true
      })
      if (signedOut) void navigate({ to: "/login", replace: true })
    } catch (error) {
      if (generation === getSession().generation) {
        setSigningOut(false)
        if (error instanceof Error) {
          handleError.call(showErrorToast, error)
        }
      }
    }
  }

  return {
    signUpMutation,
    loginMutation,
    logout,
    user,
  }
}

export { isLoggedIn }
export default useAuth
