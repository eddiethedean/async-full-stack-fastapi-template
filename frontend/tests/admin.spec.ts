import { expect, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { waitForEmailHtml } from "./utils/mailpit"
import { createUser } from "./utils/privateApi"
import { randomEmail, randomPassword } from "./utils/random"
import { logInUser } from "./utils/user"

test("Admin page is accessible and shows correct title", async ({ page }) => {
  await page.goto("/admin")
  await expect(page.getByRole("heading", { name: "Users" })).toBeVisible()
  await expect(
    page.getByText("Manage user accounts and permissions"),
  ).toBeVisible()
})

test("Add User button is visible", async ({ page }) => {
  await page.goto("/admin")
  await expect(page.getByRole("button", { name: "Add User" })).toBeVisible()
})

test("A denied refresh hides the cached user list without signing out", async ({
  page,
}) => {
  await page.goto("/admin")
  await expect(page.getByRole("table").getByText(/@/).first()).toBeVisible()
  await page.getByRole("link", { name: "Dashboard" }).click()

  await page.route(
    (url) => url.pathname === "/api/v1/users/",
    (route) =>
      route.fulfill({
        status: 403,
        json: { detail: "The user doesn't have enough privileges" },
      }),
  )
  await page.getByRole("link", { name: "Admin" }).click()

  await expect(
    page.getByText("You don't have permission to access this resource."),
  ).toBeVisible()
  await expect(page.getByRole("table")).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Add User" })).toHaveCount(0)
  await expect(page.getByTestId("user-menu")).toContainText(firstSuperuser)
})

test.describe("Admin user management", () => {
  test("Create a new user successfully", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()
    const fullName = "Test User Admin"

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill(email)
    await page.getByPlaceholder("Full name").fill(fullName)
    await page.getByPlaceholder("Password").first().fill(password)
    await page.getByPlaceholder("Password").last().fill(password)

    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User created successfully")).toBeVisible()

    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await expect(userRow).toBeVisible()
  })

  test("Create a superuser", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill(email)
    await page.getByPlaceholder("Password").first().fill(password)
    await page.getByPlaceholder("Password").last().fill(password)
    await page.getByLabel("Is superuser?").check()
    await page.getByLabel("Is active?").check()

    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User created successfully")).toBeVisible()

    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await expect(userRow.getByText("Superuser")).toBeVisible()
  })

  test("Edit a user after cancelling a draft", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()
    const originalName = "Original Name"
    const updatedName = "Updated Name"

    await page.getByRole("button", { name: "Add User" }).click()
    await page.getByPlaceholder("Email").fill(email)
    await page.getByPlaceholder("Full name").fill(originalName)
    await page.getByPlaceholder("Password").first().fill(password)
    await page.getByPlaceholder("Password").last().fill(password)
    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User created successfully")).toBeVisible()
    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    const editUser = page.getByRole("menuitem", { name: "Edit User" })
    const dialog = page.getByRole("dialog")
    await userRow.getByRole("button").click()
    await editUser.click()
    await dialog.getByLabel("Full Name").fill("Discarded name")
    await dialog.getByLabel("Set Password").fill(randomPassword())
    await dialog.getByRole("button", { name: "Cancel" }).click()
    await expect(dialog).not.toBeVisible()

    await editUser.click()
    await expect(dialog.getByLabel("Full Name")).toHaveValue(originalName)
    await expect(dialog.getByLabel("Set Password")).toHaveValue("")
    await dialog.getByLabel("Full Name").fill(updatedName)
    await dialog.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User updated successfully")).toBeVisible()
    await expect(page.getByText(updatedName)).toBeVisible()
  })

  test("Delete a user successfully", async ({ page }) => {
    await page.goto("/admin")

    const email = randomEmail()
    const password = randomPassword()

    await page.getByRole("button", { name: "Add User" }).click()
    await page.getByPlaceholder("Email").fill(email)
    await page.getByPlaceholder("Password").first().fill(password)
    await page.getByPlaceholder("Password").last().fill(password)
    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("User created successfully")).toBeVisible()

    await expect(page.getByRole("dialog")).not.toBeVisible()

    const userRow = page.getByRole("row").filter({ hasText: email })
    await userRow.getByRole("button").click()

    await page.getByRole("menuitem", { name: "Delete User" }).click()

    await page.getByRole("button", { name: "Delete" }).click()

    await expect(
      page.getByText("The user was deleted successfully"),
    ).toBeVisible()

    await expect(
      page.getByRole("row").filter({ hasText: email }),
    ).not.toBeVisible()
  })

  test("Cancel user creation", async ({ page }) => {
    await page.goto("/admin")

    await page.getByRole("button", { name: "Add User" }).click()
    await page.getByPlaceholder("Email").fill("test@example.com")

    await page.getByRole("button", { name: "Cancel" }).click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
  })

  test("Email is required and must be valid", async ({ page }) => {
    await page.goto("/admin")

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill("invalid-email")
    await page.getByPlaceholder("Email").blur()

    await expect(page.getByText("Invalid email address")).toBeVisible()
  })

  test("Password must be at least 8 characters", async ({ page }) => {
    await page.goto("/admin")

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill(randomEmail())
    await page.getByPlaceholder("Password").first().fill("short")
    await page.getByPlaceholder("Password").last().fill("short")
    await page.getByRole("button", { name: "Save" }).click()

    await expect(
      page.getByText("Password must be at least 8 characters"),
    ).toBeVisible()
  })

  test("Passwords must match", async ({ page }) => {
    await page.goto("/admin")

    await page.getByRole("button", { name: "Add User" }).click()

    await page.getByPlaceholder("Email").fill(randomEmail())
    await page.getByPlaceholder("Password").first().fill(randomPassword())
    await page.getByPlaceholder("Password").last().fill("different12345")
    await page.getByPlaceholder("Password").last().blur()

    await expect(page.getByText("The passwords don't match")).toBeVisible()
  })
})

test.describe("Admin page access control", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Non-superuser cannot access admin page", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()

    await createUser({ email, password })
    await logInUser(page, email, password)

    await page.goto("/admin")

    await expect(page.getByRole("heading", { name: "Users" })).not.toBeVisible()
    await expect(page).not.toHaveURL(/\/admin/)
  })

  test("Superuser can access admin page", async ({ page }) => {
    await logInUser(page, firstSuperuser, firstSuperuserPassword)

    await page.goto("/admin")

    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible()
  })
})

test("Invite a user to set their password", async ({
  page,
  browser,
  request,
}) => {
  const email = randomEmail()
  const password = randomPassword()
  await page.goto("/admin")
  await page.getByRole("button", { name: "Add User" }).click()
  await page.getByPlaceholder("Email").fill(email)
  await page.getByLabel("Send invitation link").check()
  await page.getByRole("button", { name: "Save" }).click()
  await expect(page.getByText("Invitation queued")).toBeVisible()

  const html = await waitForEmailHtml({ request, query: `to:${email}` })
  const link = html.match(
    /\/reset-password\?invite=true(?:&amp;|&)token=[A-Za-z0-9_-]+/,
  )?.[0]
  if (!link) throw new Error("Invitation link missing")
  const invited = await browser.newPage({
    storageState: { cookies: [], origins: [] },
  })
  try {
    await invited.goto(new URL(link.replace("&amp;", "&"), page.url()).href)
    await expect(
      invited.getByRole("heading", { name: "Set Up Account" }),
    ).toBeVisible()
    await invited.getByTestId("new-password-input").fill(password)
    await invited.getByTestId("confirm-password-input").fill(password)
    await invited.getByRole("button", { name: "Set Password" }).click()
    await expect(invited.getByText("Your account is ready")).toBeVisible()
    await logInUser(invited, email, password)
  } finally {
    await invited.close()
  }
})
