import { Text } from "@react-email/components"
import { LinkButton } from "../ui/Button"
import { Heading } from "../ui/Heading"
import { Layout } from "../ui/Layout"
import { Link } from "../ui/Link"

type NewAccountProps = {
  project_name: string
  link: string
}

export default function NewAccount({
  project_name = "{{ project_name }}",
  link = "{{ link }}",
}: NewAccountProps) {
  return (
    <Layout
      title={`${project_name} - New account`}
      preview={`Your ${project_name} account is ready`}
      project_name={project_name}
    >
      <Heading>Welcome to {project_name}!</Heading>
      <Text style={bodyTextStyle}>Hi,</Text>
      <Text style={bodyTextStyle}>
        Your account is ready. Sign in to open your dashboard.
      </Text>
      <LinkButton href={link}>Go to Dashboard</LinkButton>
      <Text style={supportingTextStyle}>
        Or copy and paste this link into your browser:
        <br />
        <Link href={link}>{link}</Link>
      </Text>
    </Layout>
  )
}

const bodyTextStyle = {
  color: "#334155",
  fontSize: "15px",
  lineHeight: "26px",
  margin: "0 0 18px",
}

const supportingTextStyle = {
  color: "#64748b",
  fontSize: "14px",
  lineHeight: "23px",
  margin: "0 0 16px",
}
