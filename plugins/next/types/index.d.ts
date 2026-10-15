// idle: nothing pending. running: /next:go is in its turn.
// armed: the skill's `handoff.py arm` call returned status ok this turn.
export type Phase = 'idle' | 'running' | 'armed'

declare module 'claude-code' {
  interface PluginState {
    next: { phase: Phase }
  }
}
