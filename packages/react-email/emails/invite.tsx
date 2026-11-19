import { Text } from "@react-email/components"
import { LinkButton } from "../ui/Button"
import { Heading } from "../ui/Heading"
import { Layout } from "../ui/Layout"

type InvitationProps = {
  project_name: string
  link: string
}

export default function Invite({
  project_name = "{{ project_name }}",
  link = "{{ link }}",
}: InvitationProps) {
  return (
    <Layout
      title={`Invitation to ${project_name}`}
      preview={`Set your password to join ${project_name}`}
      project_name={project_name}
    >
      <Heading>Join {project_name}</Heading>
      <Text>
        Set your password to accept this invitation. The link can only be used
        once and expires soon.
      </Text>
      <LinkButton href={link}>Set your password</LinkButton>
    </Layout>
  )
}
