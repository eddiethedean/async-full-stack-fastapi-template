import { zodResolver } from "@hookform/resolvers/zod"
import { useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { UsersService } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { usePrivateMutation } from "@/hooks/usePrivateMutation"
import { accountEmail } from "@/lib/account-email"
import { privateQueryKey, useSession } from "@/lib/auth"
import { handleError } from "@/utils"

const formSchema = z
  .object({
    email: accountEmail,
    full_name: z.string().optional(),
    invite: z.boolean(),
    password: z.string(),
    confirm_password: z.string(),
    is_superuser: z.boolean(),
    is_active: z.boolean(),
  })
  .superRefine((data, context) => {
    if (data.invite) return
    if (data.password.length < 8) {
      context.addIssue({
        code: "custom",
        path: ["password"],
        message: "Password must be at least 8 characters",
      })
    }
    if (data.password !== data.confirm_password) {
      context.addIssue({
        code: "custom",
        path: ["confirm_password"],
        message: "The passwords don't match",
      })
    }
  })

type FormData = z.infer<typeof formSchema>

const AddUser = () => {
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { generation } = useSession()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      email: "",
      full_name: "",
      invite: false,
      password: "",
      confirm_password: "",
      is_superuser: false,
      is_active: false,
    },
  })

  const invite = form.watch("invite")
  const mutation = usePrivateMutation({
    mutationFn: (data: FormData) => {
      const { email, full_name, is_superuser, is_active, password, invite } =
        data
      return invite
        ? UsersService.inviteUser({ body: { email, full_name, is_superuser } })
        : UsersService.createUser({
            body: { email, full_name, is_superuser, is_active, password },
          })
    },
    onSuccess: (_, data) => {
      showSuccessToast(
        data.invite ? "Invitation queued" : "User created successfully",
      )
      form.reset()
      setIsOpen(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({
        queryKey: privateQueryKey(generation, "users"),
      })
    },
  })

  const onSubmit = (data: FormData) => {
    mutation.mutate(data)
  }

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <Button className="my-4">
          <Plus className="mr-2" />
          Add User
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add User</DialogTitle>
          <DialogDescription>
            Fill in the form below to add a new user to the system.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <div className="grid gap-4 py-4">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      Email <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Email"
                        inputMode="email"
                        {...field}
                        required
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="full_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Full Name</FormLabel>
                    <FormControl>
                      <Input placeholder="Full name" type="text" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="invite"
                render={({ field }) => (
                  <FormItem className="flex items-center gap-3 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                    <FormLabel className="font-normal">
                      Send invitation link
                    </FormLabel>
                  </FormItem>
                )}
              />
              {!invite && (
                <>
                  <FormField
                    control={form.control}
                    name="password"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>
                          Set Password{" "}
                          <span className="text-destructive">*</span>
                        </FormLabel>
                        <FormControl>
                          <Input
                            placeholder="Password"
                            type="password"
                            {...field}
                            required
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="confirm_password"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>
                          Confirm Password{" "}
                          <span className="text-destructive">*</span>
                        </FormLabel>
                        <FormControl>
                          <Input
                            placeholder="Password"
                            type="password"
                            {...field}
                            required
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </>
              )}
              <FormField
                control={form.control}
                name="is_superuser"
                render={({ field }) => (
                  <FormItem className="flex items-center gap-3 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                    <FormLabel className="font-normal">Is superuser?</FormLabel>
                  </FormItem>
                )}
              />

              {!invite && (
                <FormField
                  control={form.control}
                  name="is_active"
                  render={({ field }) => (
                    <FormItem className="flex items-center gap-3 space-y-0">
                      <FormControl>
                        <Checkbox
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                      <FormLabel className="font-normal">Is active?</FormLabel>
                    </FormItem>
                  )}
                />
              )}
            </div>

            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  Cancel
                </Button>
              </DialogClose>
              <LoadingButton type="submit" loading={mutation.isPending}>
                Save
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export default AddUser
