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
  restoreBrowserSession,
  setSigningOut,
  useSession,
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
      AuthService.createBrowserSession({ body: data }),
    onSuccess: ({ data }) => {
      if (generation === getSession().generation) {
        replaceBrowserSession(data, true)
        void navigate({ to: "/" })
      }
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = async () => {
    if (generation !== getSession().generation || getSession().signingOut)
      return
    setSigningOut(true)
    try {
      await AuthService.deleteBrowserSession()
    } catch (error) {
      if (generation === getSession().generation) {
        setSigningOut(false)
        if (error instanceof Error) {
          handleError.call(showErrorToast, error)
        }
      }
      return
    }
    if (generation !== getSession().generation) return
    replaceBrowserSession(null, true)
    void navigate({ to: "/login", replace: true })
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
