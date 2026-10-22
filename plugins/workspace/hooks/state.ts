import { atom } from 'claude-code'
import type { Hook } from 'claude-code'

import type { Attached, ProjectFile } from '../types'

export type Api = Parameters<Hook<'session.start'>>[0]
export type { Attached, ProjectFile }

const key = <K extends string>(k: K) => ({ plugin: 'workspace', key: k }) as const

export const attached = atom(key('attached'), null as Attached | null)
export const task = atom(key('task'), '')
export const savedAt = atom(key('savedAt'), null as number | null)
export const turnsSinceSave = atom(key('turnsSinceSave'), 0)
export const selected = atom(key('selected'), null as string | null)
export const preview = atom(key('preview'), '')
export const files = atom(key('files'), [] as ProjectFile[])
export const marks = atom(key('marks'), {} as Record<string, 'new' | 'updated'>)
