import { expect, test } from "@playwright/test"
import { createUser } from "./utils/privateApi"
import {
  randomEmail,
  randomItemDescription,
  randomItemTitle,
  randomPassword,
} from "./utils/random"
import { logInUser } from "./utils/user"

test("Items page is accessible and shows correct title", async ({ page }) => {
  await page.goto("/items")
  await expect(
    page.getByRole("heading", { name: "Items", exact: true }),
  ).toBeVisible()
  await expect(page.getByText("Create and manage your items")).toBeVisible()
})

test("Add Item button is visible", async ({ page }) => {
  await page.goto("/items")
  await expect(page.getByRole("button", { name: "Add Item" })).toBeVisible()
})

test.describe("Items management", () => {
  test.use({ storageState: { cookies: [], origins: [] } })
  let email: string
  const password = randomPassword()

  test.beforeAll(async () => {
    email = randomEmail()
    await createUser({ email, password })
  })

  test.beforeEach(async ({ page }) => {
    await logInUser(page, email, password)
    await page.goto("/items")
  })

  test("Create a new item successfully", async ({ page }) => {
    const title = randomItemTitle()
    const description = randomItemDescription()

    await page.getByRole("button", { name: "Add Item" }).click()
    await page.getByLabel("Title").fill(title)
    await page.getByLabel("Description").fill(description)
    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("Item created successfully")).toBeVisible()
    await expect(page.getByText(title)).toBeVisible()
  })

  test("Create item with only required fields", async ({ page }) => {
    const title = randomItemTitle()

    await page.getByRole("button", { name: "Add Item" }).click()
    await page.getByLabel("Title").fill(title)
    await page.getByRole("button", { name: "Save" }).click()

    await expect(page.getByText("Item created successfully")).toBeVisible()
    await expect(page.getByText(title)).toBeVisible()
  })

  test("Cancel item creation", async ({ page }) => {
    await page.getByRole("button", { name: "Add Item" }).click()
    await page.getByLabel("Title").fill("Test Item")
    await page.getByRole("button", { name: "Cancel" }).click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
  })

  test("Title is required", async ({ page }) => {
    await page.getByRole("button", { name: "Add Item" }).click()
    await page.getByLabel("Title").fill("")
    await page.getByLabel("Title").blur()

    await expect(page.getByText("Title is required")).toBeVisible()
  })

  test.describe("Edit and Delete", () => {
    let itemTitle: string

    test.beforeEach(async ({ page }) => {
      itemTitle = randomItemTitle()

      await page.getByRole("button", { name: "Add Item" }).click()
      await page.getByLabel("Title").fill(itemTitle)
      await page.getByRole("button", { name: "Save" }).click()
      await expect(page.getByText("Item created successfully")).toBeVisible()
      await expect(page.getByRole("dialog")).not.toBeVisible()
    })

    test("Edit an item successfully", async ({ page }) => {
      const itemRow = page.getByRole("row").filter({ hasText: itemTitle })
      await itemRow.getByRole("button").last().click()
      await page.getByRole("menuitem", { name: "Edit Item" }).click()

      const updatedTitle = randomItemTitle()
      await page.getByLabel("Title").fill(updatedTitle)
      await page.getByRole("button", { name: "Save" }).click()

      await expect(page.getByText("Item updated successfully")).toBeVisible()
      await expect(page.getByText(updatedTitle)).toBeVisible()
    })

    test("Delete an item successfully", async ({ page }) => {
      const itemRow = page.getByRole("row").filter({ hasText: itemTitle })
      await itemRow.getByRole("button").last().click()
      await page.getByRole("menuitem", { name: "Delete Item" }).click()

      await page.getByRole("button", { name: "Delete" }).click()

      await expect(
        page.getByText("The item was deleted successfully"),
      ).toBeVisible()
      await expect(page.getByText(itemTitle)).not.toBeVisible()
    })
  })
})

test.describe("Items empty state", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("Shows empty state message when no items exist", async ({ page }) => {
    const email = randomEmail()
    const password = randomPassword()
    await createUser({ email, password })
    await logInUser(page, email, password)

    await page.goto("/items")

    await expect(page.getByText("You don't have any items yet")).toBeVisible()
    await expect(page.getByText("Add a new item to get started")).toBeVisible()
  })
})

test.describe("Server pagination on mobile", () => {
  test.use({
    viewport: { width: 390, height: 844 },
    storageState: { cookies: [], origins: [] },
  })

  test("Rows beyond the first 100 survive navigation and the last page recovers after deletion", async ({
    page,
  }) => {
    const email = randomEmail()
    const password = randomPassword()
    await createUser({ email, password })

    const response = await page.request.post("/api/v1/login/access-token", {
      form: { username: email, password },
    })
    expect(response.ok()).toBe(true)
    const { access_token } = await response.json()
    const headers = { Authorization: `Bearer ${access_token}` }
    const title = randomItemTitle()
    const createItem = async (title: string) => {
      const item = await page.request.post("/api/v1/items/", {
        data: { title },
        headers,
      })
      expect(item.ok()).toBe(true)
    }
    await createItem(title)
    for (let batch = 0; batch < 10; batch++) {
      await Promise.all(
        Array.from({ length: 10 }, (_, row) =>
          createItem(`Item ${batch * 10 + row + 1}`),
        ),
      )
    }

    await logInUser(page, email, password)
    await page.goto("/items?page=10&pageSize=10")
    await page.getByRole("button", { name: "Go to next page" }).click()
    await expect(page.getByText(title)).toBeVisible()
    await expect(
      page.getByText("Showing 101 to 101 of 101 entries"),
    ).toBeVisible()

    await page.reload()
    await expect(page.getByText(title)).toBeVisible()
    await page.goBack()
    await expect(
      page.getByText("Showing 91 to 100 of 101 entries"),
    ).toBeVisible()

    await page.getByRole("combobox", { name: "Rows per page" }).click()
    await page.getByRole("option", { name: "100", exact: true }).click()
    await expect(
      page.getByText("Showing 1 to 100 of 101 entries"),
    ).toBeVisible()
    await page.getByRole("button", { name: "Go to last page" }).click()

    await page
      .getByRole("row")
      .filter({ hasText: title })
      .getByRole("button")
      .last()
      .click()
    await page.getByRole("menuitem", { name: "Delete Item" }).click()
    await page.getByRole("button", { name: "Delete", exact: true }).click()
    await expect(
      page.getByText("Showing 1 to 100 of 100 entries"),
    ).toBeVisible()
    await expect(page).toHaveURL(/page=1(?:&|$)/)
  })
})
