import { createFileRoute, Outlet, redirect } from "@tanstack/react-router"
import { isAxiosError } from "axios"

import { Footer } from "@/components/Common/Footer"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import {
  currentUserOptions,
  getSession,
  queryClient,
  useSession,
} from "@/lib/auth"

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: async () => {
    const { accessToken, generation } = getSession()
    if (!accessToken) throw redirect({ to: "/login" })

    try {
      const user = await queryClient.fetchQuery(currentUserOptions(generation))
      return { user, generation }
    } catch (error) {
      if (generation !== getSession().generation)
        return { user: null, generation }
      if (isAxiosError(error) && error.response?.status === 401) {
        throw redirect({ to: "/login" })
      }
      throw error
    }
  },
})

function Layout() {
  const { accessToken, generation } = useSession()
  const context = Route.useRouteContext()
  if (!accessToken || generation !== context.generation) return null

  return (
    <SidebarProvider key={generation}>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-2 border-b bg-background px-4">
          <SidebarTrigger className="-ml-1 text-muted-foreground" />
        </header>
        <main className="flex-1 p-6 md:p-8">
          <div className="mx-auto max-w-7xl">
            <Outlet />
          </div>
        </main>
        <Footer />
      </SidebarInset>
    </SidebarProvider>
  )
}
