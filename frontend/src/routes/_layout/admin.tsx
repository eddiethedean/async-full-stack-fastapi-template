import { useQuery } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"

import { type UserPublic, UsersService } from "@/client"
import AddUser from "@/components/Admin/AddUser"
import { columns, type UserTableData } from "@/components/Admin/columns"
import { DataTable } from "@/components/Common/DataTable"
import PendingUsers from "@/components/Pending/PendingUsers"
import useAuth from "@/hooks/useAuth"
import { privateQueryKey, useSession } from "@/lib/auth"
import { paginationSearch } from "@/lib/pagination"

export const Route = createFileRoute("/_layout/admin")({
  component: Admin,
  validateSearch: paginationSearch,
  beforeLoad: ({ context }) => {
    if (context.user && !context.user.is_superuser) {
      throw redirect({
        to: "/",
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: "Admin - Async Full Stack FastAPI",
      },
    ],
  }),
})

function UsersTable() {
  const { user: currentUser } = useAuth()
  const { generation } = useSession()
  const { page, pageSize } = Route.useSearch()
  const navigate = Route.useNavigate()
  const {
    data: users,
    error,
    isPlaceholderData,
    isFetching,
  } = useQuery({
    queryFn: async ({ signal }) =>
      (
        await UsersService.readUsers({
          query: { skip: (page - 1) * pageSize, limit: pageSize },
          signal,
        })
      ).data,
    queryKey: [...privateQueryKey(generation, "users"), page, pageSize],
    placeholderData: (previous, query) =>
      query?.queryKey[1] === generation ? previous : undefined,
  })

  if (!users && error) throw error
  if (!users) return <PendingUsers />

  const tableData: UserTableData[] = (isPlaceholderData ? [] : users.data).map(
    (user: UserPublic) => ({
      ...user,
      isCurrentUser: currentUser?.id === user.id,
    }),
  )

  return (
    <DataTable
      columns={columns}
      data={tableData}
      rowCount={users.count}
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

function Admin() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Users</h1>
          <p className="text-muted-foreground">
            Manage user accounts and permissions
          </p>
        </div>
        <AddUser />
      </div>
      <UsersTable />
    </div>
  )
}
