import { z } from "zod"

export const accountEmail = z
  .string()
  .trim()
  .pipe(
    z.email({
      message: "Invalid email address",
      pattern: z.regexes.unicodeEmail,
    }),
  )
