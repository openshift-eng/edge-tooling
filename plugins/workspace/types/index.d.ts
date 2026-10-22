export type Attached = { name: string; dir: string }
export type ProjectFile = { path: string; mtimeMs: number; isRegistered: boolean }

declare module 'claude-code' {
  interface PluginState {
    workspace: {
      attached: Attached | null
      task: string
      savedAt: number | null
      turnsSinceSave: number
      selected: string | null
      preview: string
      files: ProjectFile[]
      marks: Record<string, 'new' | 'updated'>
    }
  }
}
