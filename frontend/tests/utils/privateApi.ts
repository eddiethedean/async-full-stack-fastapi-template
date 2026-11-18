import type { UserPublic } from "../../src/client"

export const createUser = async ({
  email,
  password,
  fullName = "Test User",
}: {
  email: string
  password: string
  fullName?: string
}) => {
  const baseURL = process.env.PLAYWRIGHT_BASE_URL
  const response = await fetch(new URL("/api/v1/private/users/", baseURL), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      password,
      full_name: fullName,
    }),
  })
  if (!response.ok) {
    throw new Error(`Test user setup failed with HTTP ${response.status}`)
  }
  return (await response.json()) as UserPublic
}
