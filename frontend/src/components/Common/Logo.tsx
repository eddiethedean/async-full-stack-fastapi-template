import { Link } from "@tanstack/react-router"
import { Zap } from "lucide-react"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const content = (
    <span
      role="img"
      aria-label="Async Full Stack FastAPI"
      className={cn(
        "inline-flex items-center gap-2 text-foreground",
        className,
      )}
    >
      <Zap aria-hidden="true" className="size-5 shrink-0 text-primary" />
      {variant !== "icon" && (
        <span
          className={cn(
            "text-sm font-semibold whitespace-nowrap",
            variant === "responsive" && "group-data-[collapsible=icon]:hidden",
          )}
        >
          Async Full Stack FastAPI
        </span>
      )}
    </span>
  )

  if (!asLink) {
    return content
  }

  return <Link to="/">{content}</Link>
}
