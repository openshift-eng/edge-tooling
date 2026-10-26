// The mod's type contract: self-contained (no import), as `claude plugin
// validate` requires. `hooks/model.ts` imports these from here; this file
// never imports from `hooks/*` so the contract never depends on runtime code.

export type RemoteRole = 'primary' | 'canonical' | 'other'

export type RemoteInfo = {
  name: string
  url: string
  role: RemoteRole
}

/**
 * What `git status` reports as uncommitted, in three kinds. Git tracks files, not folders: a new folder is one
 * entry in `added`, while a deleted folder is one `deleted` entry per file it held. A rename is `changed`.
 */
export type Changes = { changed: number; added: number; deleted: number }

export type WorktreeEntry = {
  path: string
  branch: string | null
  /** What changed in this worktree; absent when it could not be checked. */
  changes?: Changes
}

export type RepoInfo = {
  name: string
  path: string
  branch: string | null
  ahead: number
  behind: number
  dirtyCount: number
  /** The breakdown of `dirtyCount` for the repo's own checkout; absent when the scan failed. */
  changes?: Changes
  isClean: boolean
  defaultBranch: string | null
  remotes: RemoteInfo[]
  worktrees: WorktreeEntry[]
  role: string | null
  error?: string
}

export type WorktreeGroup = {
  branch: string
  members: { repo: string; path: string }[]
}

/**
 * What the root folder is: `workspace`, a folder of sibling repos, or `single-repo`, a folder that is
 * itself the top of one git repo (so there is nothing to route between and no workspace map to keep).
 */
export type WorkspaceMode = 'workspace' | 'single-repo'

/** One project under `<root>/projects/`, as `scripts/projects.py list` reports it. */
export type ProjectInfo = {
  /** Folder name; what the project skills take as their argument. */
  name: string
  /** The `project:` frontmatter value (the folder name when absent). */
  project: string
  type: string
  status: string
  /** `last-active` as written (`YYYY-MM-DDTHH:MM`); empty when absent. */
  lastActive: string
  jira: string
  branch: string
  domain: string
  /** `done`, `complete` or `closed`. */
  finished: boolean
  tasks: { checked: number; total: number }
}

export type WorkspaceModel = {
  root: string
  mode: WorkspaceMode
  /** Whether `<root>/dev-env.yaml` exists, i.e. the folder was set up by the workspace skills. */
  managed: boolean
  /** The projects of a managed workspace, most recently active first; empty otherwise. */
  projects: ProjectInfo[]
  /** Why `projects` could not be read (no python3, a failing script), or null. */
  projectsError: string | null
  /** Git repos only, each carrying its own worktrees (a sibling worktree folder is nested, not listed here). */
  repos: RepoInfo[]
  /** Names of the non-hidden folders under root that are not git repos. */
  otherFolders: string[]
  worktreeGroups: WorktreeGroup[]
  scannedAt: number
}

export type DocStatus = 'synced' | 'stale' | 'missing' | null

/** The workspace pane's tabs. */
export type PaneTab = 'repos' | 'projects' | 'actions' | 'settings'

/** One checklist line of a project's `CLAUDE.md`. `occurrence` tells apart items with identical text in a section. */
export type TaskItem = { text: string; checked: boolean; occurrence: number }

export type TaskSection = { heading: string; level: number; items: TaskItem[] }

/** A project's task list as `scripts/project-tasks.py list` reports it. */
export type TasksEntry =
  | { status: 'loading' }
  | { status: 'ok'; sections: TaskSection[] }
  | { status: 'error'; message: string }

export type ConsolidationSection = {
  name: string
  checked: number
  toArchive: number
  toKeep: number
  unchecked: number
  strikethrough: number
}

/** The consolidate flow of one project: a dry run to confirm, then the outcome. */
export type ConsolidationEntry =
  | { project: string; status: 'loading' }
  | { project: string; status: 'preview'; lines: number; sections: ConsolidationSection[] }
  | { project: string; status: 'note'; message: string }
  | { project: string; status: 'done'; archived: number; before: number; after: number }
  | { project: string; status: 'error'; message: string }

export type ClosingWorktree = {
  repo: string
  path: string
  branch: string
  dirty: boolean
  dirtyFiles: number
  ahead: number
  noUpstream: boolean
}

/** The close flow of one project: what `scripts/close-project.py check` found, and the notes typed so far. */
export type ClosingEntry =
  | { project: string; status: 'loading' }
  | {
      project: string
      status: 'ready'
      alreadyDone: boolean
      worktrees: ClosingWorktree[]
      notes: string
      /** What happens to the worktrees: removed when clean, kept, or discarded with their changes. */
      mode: 'remove' | 'keep' | 'discard'
    }
  | { project: string; status: 'error'; message: string }

export type NewProjectType = 'bug' | 'feature' | 'ci-testing' | 'docs' | 'analysis'

/** The new-project form while it is open. */
export type NewProjectEntry = {
  description: string
  type: NewProjectType
  jira: string
  repos: string[]
  /** The branch for the worktrees; empty means no worktree. */
  branch: string
  /** Whether the user typed the branch, so a changing description stops rewriting it. */
  isBranchEdited: boolean
  status: 'editing' | 'creating' | 'error'
  message: string
}

/** One of your open pull requests, as `gh pr list` reports it. */
export type Pr = {
  number: number
  title: string
  /** The PR's head branch. */
  branch: string
  isDraft: boolean
  url: string
}

/** What the pane knows about one repo's PRs: on its way, here (with when it was fetched), or why not. */
export type PrsEntry =
  | { status: 'loading' }
  | { status: 'ok'; prs: Pr[]; fetchedAt: number }
  | { status: 'error'; message: string }

/** The worktree whose box is open: its repo, and its path (which no two worktrees share). */
export type WorktreeRef = { repo: string; path: string }

/** What the worktree box shows beyond what a scan holds, read from the worktree when the box opens. */
export type WorktreeDetails = {
  /** The branch it tracks, like `origin/topology`; null when it tracks none. */
  upstream: string | null
  ahead: number
  behind: number
  changes: Changes
  /** The latest commit, its whole message and its date ISO 8601; null when there are none. */
  lastCommit: { hash: string; message: string; author: string; date: string } | null
}

/** What the pane knows about one worktree's details: on their way, here (with when they were read), or why not. */
export type WorktreeDetailsEntry =
  | { status: 'loading' }
  | { status: 'ok'; details: WorktreeDetails; fetchedAt: number }
  | { status: 'error'; message: string }

/** What `gh pr view` says about one PR. */
export type PrDetails = {
  number: number
  title: string
  /** `OPEN`, `MERGED` or `CLOSED`. */
  state: string
  isDraft: boolean
  author: string
  branch: string
  baseBranch: string
  url: string
  /** ISO 8601. */
  updatedAt: string
  /**
   * Review threads not yet resolved. `isLowerBound` is set when the PR has more threads than one page
   * holds, so the true count may be higher. Null when the lookup failed.
   */
  unresolved: { count: number; isLowerBound: boolean } | null
  checks: { passed: number; failed: number; pending: number }
  additions: number
  deletions: number
  changedFiles: number
}

/** What the pane knows about one PR's details: on their way, here (with when they were fetched), or why not. */
export type PrDetailsEntry =
  | { status: 'loading' }
  | { status: 'ok'; details: PrDetails; fetchedAt: number }
  | { status: 'error'; message: string }

declare module 'claude-code' {
  interface PluginState {
    'workspace': {
      model: WorkspaceModel | null
      docStatus: DocStatus
      activeTab: PaneTab
      /** The repo the Repos tab shows, by name; null until one is picked (the first repo is shown). */
      selectedRepo: string | null
      /** Whether the repo picker's list is open. */
      pickerOpen: boolean
      /** PRs per repo, by repo name. */
      prs: Record<string, PrsEntry>
      /** The worktree whose box is open beside the list; null while the box is hidden. */
      openWorktree: WorktreeRef | null
      /** What the worktree box shows beyond a scan, by worktree path. */
      worktreeDetails: Record<string, WorktreeDetailsEntry>
      /** PR details, by `<repo>#<number>`. */
      prDetails: Record<string, PrDetailsEntry>
      /** The project whose box is open on the Projects tab, by folder name; null while no box is open. */
      openProject: string | null
      /** Whether the open project's finished tasks are listed. */
      showDoneTasks: boolean
      /** Task lists per project, by folder name. */
      projectTasks: Record<string, TasksEntry>
      /** The consolidate flow in progress, if any. */
      consolidation: ConsolidationEntry | null
      /** The close flow in progress, if any. */
      closing: ClosingEntry | null
      /** The new-project form, or null while it is closed. */
      newProject: NewProjectEntry | null
    }
  }
}
