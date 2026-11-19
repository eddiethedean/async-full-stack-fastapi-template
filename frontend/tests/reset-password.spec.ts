import { expect, test } from "@playwright/test"
import { waitForEmailHtml } from "./utils/mailpit"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser, logOutUser, signUpNewUser } from "./utils/user"

test.use({ storageState: { cookies: [], origins: [] } })

const resetPath = "/reset-password?token="

test("Password Recovery title is visible", async ({ page }) => {
  await page.goto("/recover-password")

  await expect(
    page.getByRole("heading", { name: "Password Recovery" }),
  ).toBeVisible()
})

test("Input is visible, empty and editable", async ({ page }) => {
  await page.goto("/recover-password")

  await expect(page.getByTestId("email-input")).toBeVisible()
  await expect(page.getByTestId("email-input")).toHaveText("")
  await expect(page.getByTestId("email-input")).toBeEditable()
})

test("Continue button is visible", async ({ page }) => {
  await page.goto("/recover-password")

  await expect(page.getByRole("button", { name: "Continue" })).toBeVisible()
})

for (const [part, address] of [
  ["local part", (suffix: string) => `田中-${suffix}@example.com`],
  ["domain", (suffix: string) => `${suffix}@bücher.de`],
] as const) {
  test(`Account with an internationalized ${part} can sign in and request recovery`, async ({
    page,
    request,
  }) => {
    const email = address(crypto.randomUUID())
    const password = randomPassword()
    const signup = await request.post("/api/v1/users/signup", {
      data: { email, password, full_name: "Test User" },
    })
    await expect(signup).toBeOK()

    await logInUser(page, email, password)
    await logOutUser(page)
    await page.goto("/recover-password")
    await page.getByTestId("email-input").fill(email)
    const recoveryResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/password-recovery" &&
        response.request().method() === "POST",
    )
    await page.getByRole("button", { name: "Continue" }).click()
    const response = await recoveryResponse
    expect(response.ok()).toBe(true)
    expect(response.request().postDataJSON()).toEqual({ email })
    await expect(
      page.getByText(
        "If that email is registered, we sent a password recovery link",
      ),
    ).toBeVisible()
  })
}

test("User can reset password successfully using the link", async ({
  page,
  request,
}) => {
  const fullName = "Test User"
  const email = randomEmail()
  const password = randomPassword()
  const newPassword = randomPassword()

  // Sign up a new user
  await signUpNewUser(page, fullName, email, password)

  await page.goto("/recover-password")
  await page.getByTestId("email-input").fill(email)

  await page.getByRole("button", { name: "Continue" }).click()

  const emailHtml = await waitForEmailHtml({
    request,
    query: `to:${email}`,
  })

  expect(emailHtml).toContain(resetPath)
  const resetUrl = emailHtml.match(/\/reset-password\?token=[^"]+/)?.[0]
  expect(resetUrl).toBeDefined()

  // Set the new password and confirm it
  await page.goto(resetUrl!)

  await page.getByTestId("new-password-input").fill(newPassword)
  await page.getByTestId("confirm-password-input").fill(newPassword)
  await page.getByRole("button", { name: "Reset Password" }).click()
  await expect(page.getByText("Password updated successfully")).toBeVisible()

  // Check if the user is able to login with the new password
  await logInUser(page, email, newPassword)
})

test("Expired or invalid reset link", async ({ page }) => {
  const password = randomPassword()
  const invalidUrl = "/reset-password?token=invalidtoken"

  await page.goto(invalidUrl)

  await page.getByTestId("new-password-input").fill(password)
  await page.getByTestId("confirm-password-input").fill(password)
  await page.getByRole("button", { name: "Reset Password" }).click()

  await expect(page.getByText("Invalid token")).toBeVisible()
})

test("Weak new password validation", async ({ page, request }) => {
  const fullName = "Test User"
  const email = randomEmail()
  const password = randomPassword()
  const weakPassword = "123"

  // Sign up a new user
  await signUpNewUser(page, fullName, email, password)

  await page.goto("/recover-password")
  await page.getByTestId("email-input").fill(email)
  await page.getByRole("button", { name: "Continue" }).click()

  const emailHtml = await waitForEmailHtml({
    request,
    query: `to:${email}`,
  })

  expect(emailHtml).toContain(resetPath)
  const resetUrl = emailHtml.match(/\/reset-password\?token=[^"]+/)?.[0]
  expect(resetUrl).toBeDefined()

  // Set a weak new password
  await page.goto(resetUrl!)
  await page.getByTestId("new-password-input").fill(weakPassword)
  await page.getByTestId("confirm-password-input").fill(weakPassword)
  await page.getByRole("button", { name: "Reset Password" }).click()

  await expect(
    page.getByText("Password must be at least 8 characters"),
  ).toBeVisible()
})
