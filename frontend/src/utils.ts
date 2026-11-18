import { AxiosError } from "axios"

function extractErrorMessage(err: Error): string {
  if (err instanceof AxiosError) {
    if (err.response?.status === 429) {
      const seconds = Number(err.response.headers["retry-after"])
      if (Number.isFinite(seconds) && seconds > 0) {
        return `Too many attempts. Try again in ${Math.ceil(seconds)} seconds.`
      }
      return "Too many attempts. Please try again later."
    }
    const errDetail = (err.response?.data as any)?.detail
    if (Array.isArray(errDetail) && errDetail.length > 0) {
      return errDetail[0].msg
    }
    if (typeof errDetail === "string") {
      return errDetail
    }
    return err.message
  }
  return "Something went wrong."
}

export const handleError = function (this: (msg: string) => void, err: Error) {
  const errorMessage = extractErrorMessage(err)
  this(errorMessage)
}

export const getInitials = (name: string): string => {
  return name
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase()
}
