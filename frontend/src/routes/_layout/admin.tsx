import { useQuery } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"

import { UsersService } from "@/client"
import AddUser from "@/components/Admin/AddUser"
import { columns, type UserTableData } from "@/components/Admin/columns"
import { DataTable } from "@/components/Common/DataTable"
import ErrorComponent from "@/components/Common/ErrorComponent"
import PendingUsers from "@/components/Pending/PendingUsers"
import useAuth from "@/hooks/useAuth"
import { privateQueryKey, useSession } from "@/lib/auth"
import { isForbiddenError } from "@/utils"

export const Route = createFileRoute("/_layout/admin")({
  component: Admin,
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

function Admin() {
  const { user: currentUser } = useAuth()
  const { generation } = useSession()
  const { data: users, error } = useQuery({
    queryFn: async ({ signal }) =>
      (await UsersService.readUsers({ signal })).data,
    queryKey: privateQueryKey(generation, "users"),
    retry: (failureCount, error) =>
      !isForbiddenError(error) && failureCount < 3,
  })

  if (isForbiddenError(error)) return <ErrorComponent error={error} />
  if (!users && error) throw error

  const tableData: UserTableData[] | undefined = users?.data.map((user) => ({
    ...user,
    isCurrentUser: currentUser?.id === user.id,
  }))

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
      {tableData ? (
        <DataTable columns={columns} data={tableData} />
      ) : (
        <PendingUsers />
      )}
    </div>
  )
}
