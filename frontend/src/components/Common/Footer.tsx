import { projectName, projectUrl } from "@/lib/project"

export function Footer() {
  const currentYear = new Date().getFullYear()

  return (
    <footer className="border-t py-4 px-6">
      <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
        <p className="text-muted-foreground text-sm">
          {projectName} - {currentYear}
        </p>
        {projectUrl && (
          <a
            href={projectUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            Project website
          </a>
        )}
      </div>
    </footer>
  )
}
