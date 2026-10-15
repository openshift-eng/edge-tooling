// Shared types only. Atoms are NOT declared here: the engine's static check
// requires an `update`/`read` call's state reference to be a `const` literally
// declared in the same file that calls it (an `atom(...)` imported from
// another file doesn't satisfy the scan) — so register.tsx, ui.tsx and
// commands.ts each declare their own local atom for the same `{ plugin, key }`
// pair, which is what actually identifies the shared state slot.

import type {
  Changes,
  TaskItem,
  TaskSection,
  TasksEntry,
  ConsolidationSection,
  ConsolidationEntry,
  ClosingWorktree,
  ClosingEntry,
  NewProjectType,
  NewProjectEntry,
  DocStatus,
  PaneTab,
  Pr,
  PrDetails,
  PrDetailsEntry,
  PrsEntry,
  ProjectInfo,
  RemoteInfo,
  RemoteRole,
  RepoInfo,
  WorktreeDetails,
  WorktreeDetailsEntry,
  WorktreeEntry,
  WorktreeGroup,
  WorktreeRef,
  WorkspaceMode,
  WorkspaceModel,
} from '../types'

export type {
  Changes,
  TaskItem,
  TaskSection,
  TasksEntry,
  ConsolidationSection,
  ConsolidationEntry,
  ClosingWorktree,
  ClosingEntry,
  NewProjectType,
  NewProjectEntry,
  DocStatus,
  PaneTab,
  Pr,
  PrDetails,
  PrDetailsEntry,
  PrsEntry,
  ProjectInfo,
  RemoteInfo,
  RemoteRole,
  RepoInfo,
  WorktreeDetails,
  WorktreeDetailsEntry,
  WorktreeEntry,
  WorktreeGroup,
  WorktreeRef,
  WorkspaceMode,
  WorkspaceModel,
}

export const EMPTY_MODEL = (root: string): WorkspaceModel => ({
  root,
  mode: 'workspace',
  managed: false,
  projects: [],
  projectsError: null,
  repos: [],
  otherFolders: [],
  worktreeGroups: [],
  scannedAt: 0,
})
