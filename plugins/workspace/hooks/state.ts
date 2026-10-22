import { atom, read, update } from 'claude-code'
import type { Hook } from 'claude-code'

import type { Attached, ProjectFile } from '../types'

export type Api = Parameters<Hook<'session.start'>>[0]
export type { Attached, ProjectFile }


export const attached = atom({ plugin: 'workspace', key: 'attached' } as const, null as Attached | null)
export const task = atom({ plugin: 'workspace', key: 'task' } as const, '')
export const savedAt = atom({ plugin: 'workspace', key: 'savedAt' } as const, null as number | null)
export const turnsSinceSave = atom({ plugin: 'workspace', key: 'turnsSinceSave' } as const, 0)
export const selected = atom({ plugin: 'workspace', key: 'selected' } as const, null as string | null)
export const preview = atom({ plugin: 'workspace', key: 'preview' } as const, '')
export const files = atom({ plugin: 'workspace', key: 'files' } as const, [] as ProjectFile[])
export const marks = atom({ plugin: 'workspace', key: 'marks' } as const, {} as Record<string, 'new' | 'updated'>)

// The load scan reads atoms only where they are declared, so every read and
// write of them lives in this file as a function declaration.
export function getAttached($: Api) {
  return read($, attached)
}

export function getTask($: Api) {
  return read($, task)
}

export function getSavedAt($: Api) {
  return read($, savedAt)
}

export async function setTask($: Api, value: string) {
  await update($, task, () => value)
}

export async function setAttached($: Api, a: Attached, currentTask: string) {
  await update($, attached, () => a)
  await update($, task, () => currentTask)
  await update($, savedAt, () => null)
  await update($, turnsSinceSave, () => 0)
  await update($, marks, () => ({}))
}

export async function clearAttached($: Api) {
  await update($, attached, () => null)
  await update($, task, () => '')
  await update($, savedAt, () => null)
  await update($, turnsSinceSave, () => 0)
  await update($, selected, () => null)
  await update($, preview, () => '')
  await update($, files, () => [])
  await update($, marks, () => ({}))
}

export function getFiles($: Api) {
  return read($, files)
}

export function getSelected($: Api) {
  return read($, selected)
}

export function getPreview($: Api) {
  return read($, preview)
}

export function getMarks($: Api) {
  return read($, marks)
}

export async function setFiles($: Api, list: ProjectFile[]) {
  await update($, files, () => list)
}

export async function setSelection($: Api, path: string | null, text: string) {
  await update($, selected, () => path)
  await update($, preview, () => text)
}

export function getTurnsSinceSave($: Api) {
  return read($, turnsSinceSave)
}

export async function bumpTurns($: Api) {
  await update($, turnsSinceSave, n => n + 1)
}

export async function resetTurns($: Api) {
  await update($, turnsSinceSave, () => 0)
}

export async function setSavedAt($: Api, at: number) {
  await update($, savedAt, () => at)
}

export async function addMarks($: Api, added: Record<string, 'new' | 'updated'>) {
  await update($, marks, m => ({ ...m, ...added }))
}
