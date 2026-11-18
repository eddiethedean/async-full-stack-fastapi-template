import type { FullConfig } from "@playwright/test"

export default async function verifyTestBackend(config: FullConfig) {
  const baseURL = config.projects[0]?.use.baseURL
  const response = await fetch(new URL("/api/v1/private/test-mode", baseURL), {
    redirect: "error",
    signal: AbortSignal.timeout(5000),
  }).catch(() => null)
  if (
    !response?.ok ||
    (await response.json().catch(() => null))?.test_mode !== true
  ) {
    throw new Error(
      "Playwright could not verify the isolated test API. Run bun run test from the repository root.",
    )
  }
}
