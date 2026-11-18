import { useMutation, useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"

import {
  type Body_login_login_access_token as AccessToken,
  LoginService,
  type UserRegister,
  UsersService,
} from "@/client"
import {
  currentUserOptions,
  getSession,
  replaceAccessToken,
  useSession,
} from "@/lib/auth"
import { handleError } from "@/utils"
import useCustomToast from "./useCustomToast"

const isLoggedIn = () => {
  return getSession().accessToken !== null
}

const useAuth = () => {
  const navigate = useNavigate()
  const { showErrorToast } = useCustomToast()
  const { accessToken, generation } = useSession()

  const { data: user } = useQuery({
    ...currentUserOptions(generation),
    enabled: accessToken !== null,
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
    mutationFn: (data: AccessToken) =>
      LoginService.loginAccessToken({ body: data }),
    onSuccess: ({ data }) => {
      if (replaceAccessToken(data.access_token, generation)) {
        void navigate({ to: "/" })
      }
    },
    onError: handleError.bind(showErrorToast),
  })

  const logout = () => {
    if (replaceAccessToken(null, generation)) {
      void navigate({ to: "/login", replace: true })
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
