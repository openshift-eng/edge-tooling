export const meta = {
  name: 'parallel-agents',
  description: 'Run agents in parallel and report success counts',
  phases: [
    { title: 'Analyze', detail: 'Per-job agent execution' },
  ],
}

let parsed
try {
  parsed = typeof args === 'string' ? JSON.parse(args) : args
} catch (e) {
  log('Invalid args: ' + (e.message || String(e)))
  return { analyzed: 0, failed: 0, total: 0, results: [] }
}
if (!parsed || !Array.isArray(parsed.jobs)) {
  log('Invalid args: expected object with jobs array, got ' + JSON.stringify(parsed))
  return { analyzed: 0, failed: 0, total: 0, results: [] }
}

const phaseName = parsed.phaseName || 'Analyze'
phase(phaseName)
log('Running ' + parsed.jobs.length + ' agents in parallel...')

// Use Promise.all instead of parallel() — parallel() caps concurrency at min(16, cpu_cores - 2)
const promises = parsed.jobs.map(function (job) {
  if (!job.prompt) {
    log('Skipping job without prompt: ' + (job.label || 'unknown'))
    return Promise.resolve(null)
  }
  return agent(job.prompt, {
    label: job.label,
    phase: phaseName,
    agentType: parsed.agentType,
  }).catch(function () { return null })
})

const results = await Promise.all(promises)

parsed.jobs.forEach(function (job, i) {
  if (!results[i]) log('Agent failed: ' + job.label)
})

const succeeded = results.filter(Boolean).length
log('Complete: ' + succeeded + '/' + parsed.jobs.length + ' agents succeeded')

return { analyzed: succeeded, failed: parsed.jobs.length - succeeded, total: parsed.jobs.length, results: results }
