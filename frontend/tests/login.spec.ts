import { expect, type Page, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { createUser } from "./utils/privateApi"
import { randomEmail, randomItemTitle, randomPassword } from "./utils/random.ts"

test.use({ storageState: { cookies: [], origins: [] } })

const fillForm = async (page: Page, email: string, password: string) => {
  await page.getByTestId("email-input").fill(email)
  await page.getByTestId("password-input").fill(password)
}

const verifyInput = async (page: Page, testId: string) => {
  const input = page.getByTestId(testId)
  await expect(input).toBeVisible()
  await expect(input).toHaveText("")
  await expect(input).toBeEditable()
}

test("Inputs are visible, empty and editable", async ({ page }) => {
  await page.goto("/login")

  await verifyInput(page, "email-input")
  await verifyInput(page, "password-input")
})

test("Log In button is visible", async ({ page }) => {
  await page.goto("/login")

  await expect(page.getByRole("button", { name: "Log In" })).toBeVisible()
})

test("Forgot Password link is visible", async ({ page }) => {
  await page.goto("/login")

  await expect(
    page.getByRole("link", { name: "Forgot your password?" }),
  ).toBeVisible()
})

test("Log in with valid email and password ", async ({ page }) => {
  await page.goto("/login")

  await fillForm(page, firstSuperuser, firstSuperuserPassword)
  await page.getByRole("button", { name: "Log In" }).click()

  await page.waitForURL("/")
  const cookie = (await page.context().cookies()).find((cookie) =>
    cookie.name.endsWith("asf_session"),
  )
  expect(cookie?.httpOnly).toBe(true)
  expect(
    await page.evaluate(() => localStorage.getItem("access_token")),
  ).toBeNull()
  await page.reload()

  await expect(
    page.getByText("Welcome back, nice to see you again!"),
  ).toBeVisible()
})

test("Log in with invalid email", async ({ page }) => {
  await page.goto("/login")

  await fillForm(page, "invalidemail", firstSuperuserPassword)
  await page.getByRole("button", { name: "Log In" }).click()

  await expect(page.getByText("Invalid email address")).toBeVisible()
})

test("Log in with invalid password", async ({ page }) => {
  const password = randomPassword()

  await page.goto("/login")
  await fillForm(page, firstSuperuser, password)
  await page.getByRole("button", { name: "Log In" }).click()

  await expect(page.getByText("Incorrect email or password")).toBeVisible()
})

test("Successful log out", async ({ page }) => {
  await page.goto("/login")

  await fillForm(page, firstSuperuser, firstSuperuserPassword)
  await page.getByRole("button", { name: "Log In" }).click()

  await page.waitForURL("/")

  await expect(
    page.getByText("Welcome back, nice to see you again!"),
  ).toBeVisible()

  await page.getByTestId("user-menu").click()
  await page.getByRole("menuitem", { name: "Log out" }).click()
  await page.waitForURL("/login")
})

test("Logged-out user cannot access protected routes", async ({ page }) => {
  await page.goto("/login")

  await fillForm(page, firstSuperuser, firstSuperuserPassword)
  await page.getByRole("button", { name: "Log In" }).click()

  await page.waitForURL("/")

  await expect(
    page.getByText("Welcome back, nice to see you again!"),
  ).toBeVisible()

  await page.getByTestId("user-menu").click()
  await page.getByRole("menuitem", { name: "Log out" }).click()
  await page.waitForURL("/login")

  await page.goto("/settings")
  await page.waitForURL("/login")
})

test("Clearing the session cookie returns the browser to login", async ({
  page,
}) => {
  await page.goto("/login")
  await fillForm(page, firstSuperuser, firstSuperuserPassword)
  await page.getByRole("button", { name: "Log In" }).click()
  await page.waitForURL("/")
  await page.context().clearCookies()
  await page.goto("/settings")
  await page.waitForURL("/login")
  await expect(page).toHaveURL("/login")
})

test("A late response from the previous account cannot sign out the next account", async ({
  page,
}) => {
  const first = randomEmail()
  const second = randomEmail()
  const password = randomPassword()
  const title = randomItemTitle()
  await createUser({ email: first, password })
  await createUser({ email: second, password })

  await page.goto("/login")
  await fillForm(page, first, password)
  await page.getByRole("button", { name: "Log In" }).click()
  await page.getByRole("link", { name: "Items" }).click()
  await page.getByRole("button", { name: "Add Item" }).click()
  await page.getByLabel("Title").fill(title)
  await page.getByRole("button", { name: "Save" }).click()
  await expect(page.getByText(title)).toBeVisible()

  await page.getByTestId("user-menu").click()
  await page.getByRole("menuitem", { name: "User Settings" }).click()

  let releaseResponse = () => {}
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve
  })
  await page.route("**/api/v1/users/me", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback()
    await responseGate
    await route.fulfill({
      status: 401,
      json: { detail: "Could not validate credentials" },
    })
  })

  try {
    await page.getByRole("button", { name: "Edit", exact: true }).click()
    await page.getByLabel("Full name").fill("Changed")
    const pendingRequest = page.waitForRequest(
      (request) =>
        request.method() === "PATCH" && request.url().endsWith("/users/me"),
    )
    await page.getByRole("button", { name: "Save" }).click()
    await pendingRequest

    await page.getByTestId("user-menu").click()
    await page.getByRole("menuitem", { name: "Log out" }).press("Enter")
    await expect(page).toHaveURL("/login")
    await fillForm(page, second, password)
    await page.getByRole("button", { name: "Log In" }).click()
    await page.getByRole("link", { name: "Items" }).click()

    const response = page.waitForResponse(
      (result) =>
        result.request().method() === "PATCH" &&
        result.url().endsWith("/users/me"),
    )
    releaseResponse()
    await response

    await expect(page.getByTestId("user-menu")).toContainText(second)
    await expect(page.getByText(title)).toHaveCount(0)
    await expect(page).toHaveURL("/items")
  } finally {
    releaseResponse()
  }
})
