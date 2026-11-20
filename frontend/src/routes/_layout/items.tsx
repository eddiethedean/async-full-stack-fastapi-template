import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Search } from "lucide-react"

import { ItemsService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import AddItem from "@/components/Items/AddItem"
import { columns } from "@/components/Items/columns"
import PendingItems from "@/components/Pending/PendingItems"
import { privateQueryKey, useSession } from "@/lib/auth"
import { paginationSearch } from "@/lib/pagination"
import { projectName } from "@/lib/project"

export const Route = createFileRoute("/_layout/items")({
  component: Items,
  validateSearch: paginationSearch,
  head: () => ({
    meta: [
      {
        title: `Items - ${projectName}`,
      },
    ],
  }),
})

function ItemsTable() {
  const { generation } = useSession()
  const { page, pageSize } = Route.useSearch()
  const navigate = Route.useNavigate()
  const {
    data: items,
    error,
    isPlaceholderData,
    isFetching,
  } = useQuery({
    queryFn: async ({ signal }) =>
      (
        await ItemsService.readItems({
          query: { skip: (page - 1) * pageSize, limit: pageSize },
          signal,
        })
      ).data,
    queryKey: [...privateQueryKey(generation, "items"), page, pageSize],
    placeholderData: (previous, query) =>
      query?.queryKey[1] === generation ? previous : undefined,
  })

  if (!items && error) throw error
  if (!items) return <PendingItems />

  if (items.count === 0 && page === 1 && !isPlaceholderData) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <Search className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">You don't have any items yet</h3>
        <p className="text-muted-foreground">Add a new item to get started</p>
      </div>
    )
  }

  return (
    <DataTable
      columns={columns}
      data={isPlaceholderData ? [] : items.data}
      rowCount={items.count}
      pagination={{ pageIndex: page - 1, pageSize }}
      onPaginationChange={(next, replace) => {
        void navigate({
          search: { page: next.pageIndex + 1, pageSize: next.pageSize },
          replace,
        })
      }}
      loading={isPlaceholderData}
      refreshing={isFetching || isPlaceholderData}
    />
  )
}

function Items() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Items</h1>
          <p className="text-muted-foreground">Create and manage your items</p>
        </div>
        <AddItem />
      </div>
      <ItemsTable />
    </div>
  )
}
