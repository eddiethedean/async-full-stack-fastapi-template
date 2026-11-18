import { type UseMutationOptions, useMutation } from "@tanstack/react-query"

import { getSession, privateQueryKey, useSession } from "@/lib/auth"

export function usePrivateMutation<TData, TError, TVariables>(
  options: UseMutationOptions<TData, TError, TVariables> & {
    mutationFn: NonNullable<
      UseMutationOptions<TData, TError, TVariables>["mutationFn"]
    >
  },
) {
  const { generation } = useSession()
  const isCurrent = () => generation === getSession().generation

  return useMutation({
    ...options,
    mutationKey: privateQueryKey(generation),
    mutationFn: (...args: Parameters<typeof options.mutationFn>) => {
      if (!isCurrent()) return Promise.reject(new Error("Session changed"))
      return options.mutationFn(...args)
    },
    onSuccess: (...args) => {
      if (isCurrent()) return options.onSuccess?.(...args)
    },
    onError: (...args) => {
      if (isCurrent()) return options.onError?.(...args)
    },
    onSettled: (...args) => {
      if (isCurrent()) return options.onSettled?.(...args)
    },
  })
}
