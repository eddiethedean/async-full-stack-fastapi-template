import { expect, test } from "@playwright/test"

test.describe("public project branding", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("uses build-time branding in the HTML shell and sign-in page", async ({
    page,
  }) => {
    const response = await page.goto("/login")
    expect(await response?.text()).toContain("<title>Starter &amp; Co</title>")
    await expect(page).toHaveTitle("Log In - Starter & Co")
    await expect(page.getByRole("img", { name: "Starter & Co" })).toBeVisible()
    await expect(page.locator("footer")).toContainText("Starter & Co")
    await expect(
      page.getByRole("link", { name: "Project website" }),
    ).toHaveAttribute("href", "https://example.com/project")
  })
})

test("uses build-time branding on the authenticated dashboard", async ({
  page,
}) => {
  await page.goto("/")
  await expect(page).toHaveTitle("Dashboard - Starter & Co")
  await expect(page.getByRole("img", { name: "Starter & Co" })).toBeVisible()
  await expect(page.locator("footer")).toContainText("Starter & Co")
})
