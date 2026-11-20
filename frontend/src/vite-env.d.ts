/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string
  readonly VITE_PROJECT_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare const __PROJECT_NAME__: string
