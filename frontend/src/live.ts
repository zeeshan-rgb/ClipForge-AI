import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { Job, Stats } from './api'

type LiveEvent =
  | { type: 'hello'; version: string }
  | { type: 'job'; job: Job }
  | { type: 'project'; id: number }
  | { type: 'outreach' }
  | { type: 'schedule' }

const TERMINAL = new Set(['done', 'failed', 'cancelled'])

function upsert(list: Job[] | undefined, job: Job): Job[] | undefined {
  if (!list) return list
  const i = list.findIndex((j) => j.id === job.id)
  if (i === -1) return [job, ...list]
  const next = list.slice()
  next[i] = job
  return next
}

function applyJob(qc: QueryClient, job: Job) {
  // Progress ticks are written straight into the cache: no refetch per tick.
  qc.setQueriesData<Job[]>({ queryKey: ['jobs'] }, (old) => {
    if (!old) return old
    return old.some((j) => j.id === job.id) ? upsert(old, job) : old
  })
  qc.setQueryData<Stats>(['stats'], (old) => {
    if (!old) return old
    const active = old.active_jobs.filter((j) => j.id !== job.id)
    if (!TERMINAL.has(job.status)) active.push(job)
    return { ...old, active_jobs: active }
  })
  const known = qc
    .getQueriesData<Job[]>({ queryKey: ['jobs'] })
    .some(([, list]) => list?.some((j) => j.id === job.id))
  if (!known || TERMINAL.has(job.status)) {
    void qc.invalidateQueries({ queryKey: ['jobs'] })
    void qc.invalidateQueries({ queryKey: ['stats'] })
  }
}

/** Keeps the query cache in step with the server over one WebSocket. */
export function useLiveUpdates(): boolean {
  const qc = useQueryClient()
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    let ws: WebSocket | null = null
    let timer: number | undefined
    let closed = false
    let attempt = 0

    const connect = () => {
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${scheme}://${location.host}/api/ws`)
      ws.onopen = () => {
        setConnected(true)
        if (attempt > 0) void qc.invalidateQueries() // catch up after a gap
        attempt = 0
      }
      ws.onmessage = (msg) => {
        const event = JSON.parse(msg.data as string) as LiveEvent
        if (event.type === 'job') applyJob(qc, event.job)
        else if (event.type === 'project') {
          void qc.invalidateQueries({ queryKey: ['projects'] })
          void qc.invalidateQueries({ queryKey: ['project', event.id] })
          void qc.invalidateQueries({ queryKey: ['stats'] })
          void qc.invalidateQueries({ queryKey: ['shorts'] })
          void qc.invalidateQueries({ queryKey: ['schedule'] })
        } else if (event.type === 'schedule') {
          void qc.invalidateQueries({ queryKey: ['schedule'] })
          void qc.invalidateQueries({ queryKey: ['shorts'] })
        } else if (event.type === 'outreach') {
          void qc.invalidateQueries({ queryKey: ['outreach'] })
          void qc.invalidateQueries({ queryKey: ['streamers'] })
          void qc.invalidateQueries({ queryKey: ['approved-streamers'] })
        }
      }
      ws.onclose = () => {
        setConnected(false)
        if (closed) return
        attempt += 1
        timer = window.setTimeout(connect, Math.min(5000, 500 * attempt))
      }
      ws.onerror = () => ws?.close()
    }

    connect()
    return () => {
      closed = true
      window.clearTimeout(timer)
      ws?.close()
    }
  }, [qc])

  return connected
}
