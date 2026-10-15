// The Projects and Actions tabs. Pure drawing: the elements come from the render hook's own
// `$.ui.resolve(e)`, the data is plain, and every press is a callback made in ui.tsx, where the `$` calls
// stay at their own call sites (the engine's static check forbids passing `$` through a function here).

import type { ElementTable } from 'claude-code'

import type {
  ClosingEntry,
  ConsolidationEntry,
  NewProjectEntry,
  ProjectInfo,
  RepoInfo,
  TasksEntry,
  WorkspaceModel,
} from './model'
import {
  NEW_PROJECT_TYPES,
  canCreateProject,
  needsAttention,
  projectRowText,
  rowSkills,
  worktreeStatusText,
} from './project-actions'
import type { SkillName, WorktreeMode } from './project-actions'

export type Els = ElementTable

export type ProjectsActions = {
  openProject: (name: string) => void
  launch: (skill: SkillName, args?: string) => void
  openForm: () => void
  // tasks
  toggleShowDone: () => void
  toggleTask: (project: string, section: string, text: string, occurrence: number) => void
  removeTask: (project: string, section: string, text: string, occurrence: number) => void
  addTask: (project: string, section: string, text: string) => void
  // consolidate
  startConsolidate: (project: string) => void
  confirmConsolidate: (project: string) => void
  dismissConsolidate: () => void
  // close
  startClose: (project: string) => void
  setCloseNotes: (text: string) => void
  setCloseMode: (mode: WorktreeMode) => void
  confirmClose: () => void
  dismissClose: () => void
  // new project
  setForm: (patch: Partial<NewProjectEntry>) => void
  toggleFormRepo: (name: string) => void
  createProject: () => void
  dismissForm: () => void
}

export type ProjectsData = {
  model: WorkspaceModel
  /** Whether the surface draws text fields (mobile does not). */
  hasFields: boolean
  now: number
  openProject: string | null
  showDone: boolean
  tasks: Record<string, TasksEntry>
  consolidation: ConsolidationEntry | null
  closing: ClosingEntry | null
  form: NewProjectEntry | null
}

const SKILL_LABELS: Record<SkillName, string> = {
  'resume-project': 'Resume',
  'update-project': 'Update',
  handoff: 'Hand off',
  'consolidate-project': 'Consolidate',
  'close-project': 'Close',
  'setup-environment': 'Set up workspace',
  'create-domain': 'Create domain',
  'update-domain': 'Update domain',
  'new-project': 'New project',
  'auto-update': 'Auto-update notes',
}

/** The project's name in the list: a triangle that points down while its box is open. */
export function projectToggleLabel(name: string, isOpen: boolean): string {
  return `${isOpen ? '▾' : '▸'} ${name}`
}

export function renderProjectsTab(els: Els, data: ProjectsData, act: ProjectsActions) {
  const { Box, Text, Button } = els
  const { model } = data

  if (!model.managed) {
    return (
      <Box flexDirection="column">
        <Text>Not a managed workspace.</Text>
        <Text dimColor>{`There is no dev-env.yaml in ${model.root}, so there are no projects to list.`}</Text>
        <Button key="setup-environment" label="Set up workspace" onPress={() => act.launch('setup-environment')} />
      </Box>
    )
  }

  const open = model.projects.find(p => p.name === data.openProject)
  return (
    <Box flexDirection="column">
      <Box columnGap={1}>
        <Button key="new-project-open" label="New project" onPress={act.openForm} />
      </Box>
      {data.form !== null && renderNewProjectForm(els, data, act, data.form)}
      {model.projectsError !== null && <Text color="yellow">{`Projects unavailable: ${model.projectsError}`}</Text>}
      {model.projectsError === null && model.projects.length === 0 && data.form === null && (
        <Text dimColor>No projects yet.</Text>
      )}
      {model.projects.map(p => renderProjectRow(els, data, act, p))}
      {open !== undefined && renderProjectBox(els, data, act, open)}
    </Box>
  )
}

function renderProjectRow(els: Els, data: ProjectsData, act: ProjectsActions, p: ProjectInfo) {
  const { Box, Text, Button } = els
  const isOpen = p.name === data.openProject
  const skills = rowSkills(p.finished)
  return (
    <Box key={`project-row-${p.name}`} flexDirection="column">
      <Button
        key={`project-${p.name}`}
        plain
        label={projectToggleLabel(p.name, isOpen)}
        onPress={() => act.openProject(p.name)}
      />
      <Box paddingLeft={2} flexDirection="column">
        <Text dimColor>{projectRowText(p, data.now)}</Text>
        <Box columnGap={1} flexWrap="wrap">
          {skills.map(skill => (
            <Button
              key={`${skill}-${p.name}`}
              label={SKILL_LABELS[skill]}
              onPress={() => act.launch(skill, p.name)}
            />
          ))}
          {!p.finished && (
            <Button key={`consolidate-${p.name}`} label="Consolidate" onPress={() => act.startConsolidate(p.name)} />
          )}
          {!p.finished && <Button key={`close-${p.name}`} label="Close" onPress={() => act.startClose(p.name)} />}
        </Box>
      </Box>
    </Box>
  )
}

function renderProjectBox(els: Els, data: ProjectsData, act: ProjectsActions, p: ProjectInfo) {
  const { Box, Text, Button } = els
  const consolidation = data.consolidation?.project === p.name ? data.consolidation : null
  const closing = data.closing?.project === p.name ? data.closing : null
  return (
    <Box key="project-box" borderStyle="single" paddingX={1} flexDirection="column">
      <Box justifyContent="space-between">
        <Text bold>{p.project}</Text>
        <Box flexShrink={0}>
          <Button key="close-project-box" plain label="✕" onPress={() => act.openProject(p.name)} />
        </Box>
      </Box>
      {consolidation !== null && renderConsolidation(els, act, consolidation)}
      {closing !== null && renderClosing(els, data, act, closing)}
      {renderTasks(els, data, act, p)}
    </Box>
  )
}

function renderTasks(els: Els, data: ProjectsData, act: ProjectsActions, p: ProjectInfo) {
  const { Box, Text, Button, Input } = els
  const entry = data.tasks[p.name]
  if (entry === undefined || entry.status === 'loading') return <Text dimColor>Loading tasks…</Text>
  if (entry.status === 'error') return <Text color="yellow">{entry.message}</Text>

  const all = entry.sections.flatMap(s => s.items)
  const done = all.filter(i => i.checked).length
  return (
    <Box key="tasks" flexDirection="column">
      <Box justifyContent="space-between">
        <Text bold>{`Tasks (${done}/${all.length})`}</Text>
        {done > 0 && (
          <Box flexShrink={0}>
            <Button
              key="tasks-show-done"
              plain
              label={data.showDone ? 'Hide done' : `Show done (${done})`}
              onPress={act.toggleShowDone}
            />
          </Box>
        )}
      </Box>
      {all.length === 0 && <Text dimColor>No tasks in this project's CLAUDE.md.</Text>}
      {entry.sections.map((section, si) => (
        <Box key={`task-section-${si}`} flexDirection="column">
          <Text bold dimColor>{section.heading}</Text>
          {[...section.items.filter(i => !i.checked), ...(data.showDone ? section.items.filter(i => i.checked) : [])].map(
            (item, ii) => (
              <Box key={`task-${si}-${ii}`} columnGap={1}>
                <Button
                  key={`task-toggle-${si}-${ii}`}
                  plain
                  label={`${item.checked ? '☑' : '☐'} ${item.text}`}
                  onPress={() => act.toggleTask(p.name, section.heading, item.text, item.occurrence)}
                />
                <Box flexShrink={0}>
                  <Button
                    key={`task-remove-${si}-${ii}`}
                    plain
                    label="x"
                    onPress={() => act.removeTask(p.name, section.heading, item.text, item.occurrence)}
                  />
                </Box>
              </Box>
            ),
          )}
          {data.hasFields && (
            <Input
              key={`task-add-${si}`}
              label="Add"
              placeholder="New task, then Enter"
              value=""
              submitLabel="add"
              onSubmit={(text: string) => act.addTask(p.name, section.heading, text)}
            />
          )}
        </Box>
      ))}
    </Box>
  )
}

function renderConsolidation(els: Els, act: ProjectsActions, entry: ConsolidationEntry) {
  const { Box, Text, Button } = els
  const dismiss = <Button key="consolidate-dismiss" label="Dismiss" onPress={act.dismissConsolidate} />
  return (
    <Box key="consolidation" borderStyle="round" paddingX={1} flexDirection="column">
      <Text bold>Consolidate CLAUDE.md</Text>
      {entry.status === 'loading' && <Text dimColor>Checking…</Text>}
      {entry.status === 'note' && <Text>{entry.message}</Text>}
      {entry.status === 'error' && <Text color="yellow">{entry.message}</Text>}
      {entry.status === 'done' && (
        <Text>{`Archived ${entry.archived} items; CLAUDE.md went from ${entry.before} to ${entry.after} lines.`}</Text>
      )}
      {entry.status === 'preview' && (
        <Box flexDirection="column">
          <Text dimColor>{`CLAUDE.md is ${entry.lines} lines. Checked items move to progress-archive.md:`}</Text>
          {entry.sections.map(s => (
            <Text key={`consolidate-${s.name}`}>{`• ${s.name}: archive ${s.toArchive}, keep ${s.toKeep} (${s.unchecked} open)`}</Text>
          ))}
          <Box columnGap={1}>
            <Button key="consolidate-confirm" label="Archive" onPress={() => act.confirmConsolidate(entry.project)} />
            <Button key="consolidate-cancel" label="Cancel" onPress={act.dismissConsolidate} />
          </Box>
        </Box>
      )}
      {entry.status !== 'preview' && entry.status !== 'loading' && dismiss}
    </Box>
  )
}

function renderClosing(els: Els, data: ProjectsData, act: ProjectsActions, entry: ClosingEntry) {
  const { Box, Text, Button, Input } = els
  return (
    <Box key="closing" borderStyle="round" paddingX={1} flexDirection="column">
      <Text bold>Close project</Text>
      {entry.status === 'loading' && <Text dimColor>Checking worktrees…</Text>}
      {entry.status === 'error' && (
        <Box flexDirection="column">
          <Text color="yellow">{entry.message}</Text>
          <Button key="close-dismiss" label="Dismiss" onPress={act.dismissClose} />
        </Box>
      )}
      {entry.status === 'ready' && (
        <Box flexDirection="column">
          {entry.alreadyDone && <Text dimColor>Already marked done; closing again updates the notes.</Text>}
          {entry.worktrees.length > 0 && <Text bold>Worktrees</Text>}
          {entry.worktrees.map(w => (
            <Text key={`close-wt-${w.path}`} color={needsAttention(w) ? 'yellow' : undefined}>
              {`• ${w.repo} · ${w.branch || '(detached)'} — ${worktreeStatusText(w)}`}
            </Text>
          ))}
          {entry.worktrees.some(needsAttention) && (
            <Box flexDirection="column">
              <Text color="yellow">Some worktrees hold work that removing would lose.</Text>
              <Box columnGap={1}>
                <Button
                  key="close-mode-keep"
                  variant={entry.mode === 'keep' ? 'primary' : 'secondary'}
                  label="Keep worktrees"
                  onPress={() => act.setCloseMode('keep')}
                />
                <Button
                  key="close-mode-discard"
                  variant={entry.mode === 'discard' ? 'primary' : 'secondary'}
                  label="Discard changes"
                  onPress={() => act.setCloseMode('discard')}
                />
              </Box>
            </Box>
          )}
          {data.hasFields && (
            <Input
              key="close-notes"
              label="Notes"
              placeholder="Outcome, PR links (optional)"
              value={entry.notes}
              onInput={(text: string) => act.setCloseNotes(text)}
              onSubmit={(text: string) => act.setCloseNotes(text)}
            />
          )}
          <Box columnGap={1}>
            <Button key="close-confirm" label="Close project" onPress={act.confirmClose} />
            <Button key="close-cancel" label="Cancel" onPress={act.dismissClose} />
          </Box>
        </Box>
      )}
    </Box>
  )
}

function renderNewProjectForm(els: Els, data: ProjectsData, act: ProjectsActions, form: NewProjectEntry) {
  const { Box, Text, Button, Input } = els
  const repos: RepoInfo[] = data.model.mode === 'single-repo' ? [] : data.model.repos
  if (!data.hasFields) {
    return (
      <Box key="new-project-form" borderStyle="round" paddingX={1} flexDirection="column">
        <Text bold>New project</Text>
        <Text dimColor>This app draws no text fields, so start it as a skill instead.</Text>
        <Box columnGap={1}>
          <Button key="np-skill" label="Run /workspace:new-project" onPress={() => act.launch('new-project')} />
          <Button key="np-cancel" label="Cancel" onPress={act.dismissForm} />
        </Box>
      </Box>
    )
  }
  return (
    <Box key="new-project-form" borderStyle="round" paddingX={1} flexDirection="column">
      <Text bold>New project</Text>
      <Input
        key="np-description"
        label="Task"
        placeholder="What are you working on?"
        value={form.description}
        autoFocus
        onInput={(text: string) => act.setForm({ description: text })}
        onSubmit={(text: string) => act.setForm({ description: text })}
      />
      <Box columnGap={1} flexWrap="wrap">
        {NEW_PROJECT_TYPES.map(t => (
          <Button
            key={`np-type-${t.value}`}
            variant={form.type === t.value ? 'primary' : 'secondary'}
            label={t.label}
            onPress={() => act.setForm({ type: t.value })}
          />
        ))}
      </Box>
      <Input
        key="np-jira"
        label="JIRA"
        placeholder="Ticket URL (optional)"
        value={form.jira}
        onInput={(text: string) => act.setForm({ jira: text })}
        onSubmit={(text: string) => act.setForm({ jira: text })}
      />
      {repos.length > 0 && (
        <Box flexDirection="column">
          <Text bold>Repos</Text>
          <Box columnGap={1} flexWrap="wrap">
            {repos.map(r => (
              <Button
                key={`np-repo-${r.name}`}
                plain
                label={`${form.repos.includes(r.name) ? '●' : '○'} ${r.name}`}
                onPress={() => act.toggleFormRepo(r.name)}
              />
            ))}
          </Box>
        </Box>
      )}
      <Input
        key="np-branch"
        label="Branch"
        placeholder="Worktree branch (blank for none)"
        value={form.branch}
        onInput={(text: string) => act.setForm({ branch: text, isBranchEdited: true })}
        onSubmit={(text: string) => act.setForm({ branch: text, isBranchEdited: true })}
      />
      {form.message !== '' && <Text color={form.status === 'error' ? 'yellow' : undefined}>{form.message}</Text>}
      <Box columnGap={1}>
        <Button
          key="np-create"
          label={form.status === 'creating' ? 'Creating…' : 'Create'}
          onPress={() => canCreateProject(form) && act.createProject()}
        />
        <Button key="np-cancel" label="Cancel" onPress={act.dismissForm} />
      </Box>
    </Box>
  )
}

const ACTIONS: readonly { skill: SkillName; description: string }[] = [
  { skill: 'setup-environment', description: 'Clone a domain’s repos here, or wrap this repo as a workspace.' },
  { skill: 'create-domain', description: 'Build a workspace from any set of repos, with per-repo context.' },
  { skill: 'update-domain', description: 'Feed a project’s lessons back into its domain.' },
  { skill: 'auto-update', description: 'Save project notes about every 50 minutes while you work.' },
]

export function renderActionsTab(els: Els, act: ProjectsActions) {
  const { Box, Text, Button } = els
  return (
    <Box flexDirection="column">
      <Box key="action-new-project" flexDirection="column">
        <Button key="action-new-project-button" label="New project" onPress={act.openForm} />
        <Text dimColor>Start a project folder, with a worktree if you give it a branch.</Text>
      </Box>
      {ACTIONS.map(a => (
        <Box key={`action-${a.skill}`} flexDirection="column">
          <Text> </Text>
          <Button key={`action-${a.skill}-button`} label={SKILL_LABELS[a.skill]} onPress={() => act.launch(a.skill)} />
          <Text dimColor>{a.description}</Text>
        </Box>
      ))}
    </Box>
  )
}
