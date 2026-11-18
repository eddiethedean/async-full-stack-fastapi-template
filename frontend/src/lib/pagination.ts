export const pageSizes = [5, 10, 25, 50, 100]

export function paginationSearch(search: Record<string, unknown>) {
  const pageSize =
    typeof search.pageSize === "number" && pageSizes.includes(search.pageSize)
      ? search.pageSize
      : 10
  const page =
    typeof search.page === "number" &&
    Number.isSafeInteger(search.page) &&
    search.page >= 1 &&
    search.page <= Math.floor(Number.MAX_SAFE_INTEGER / pageSize)
      ? search.page
      : 1

  return { page, pageSize }
}
