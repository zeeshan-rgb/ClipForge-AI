import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ExportResult, type PublishChecklist, type ShortRow } from '../api'
import { QualityList, ScoreBadge } from '../components/Quality'
import { CATEGORY_LABEL, RIGHTS } from '../format'
import { Badge, Empty, ErrorNote, PageHeader, Section, Spinner } from '../ui'

function TextEditor({ row }: { row: ShortRow }) {
  const qc = useQueryClient()
  const meta = row.meta
  const initial = useMemo(
    () => ({
      title: meta?.title ?? '',
      description: meta?.description ?? '',
      hashtags: (meta?.hashtags ?? []).join(' '),
      pinned_comment: meta?.pinned_comment ?? '',
    }),
    [meta],
  )
  const [draft, setDraft] = useState(initial)
  useEffect(() => setDraft(initial), [initial])
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial)
  const refresh = () => void qc.invalidateQueries({ queryKey: ['shorts'] })
  const save = useMutation({
    mutationFn: () =>
      api.editClipText(row.project.id, row.id, {
        ...draft,
        hashtags: draft.hashtags.split(/[\s,]+/).filter(Boolean),
      }),
    onSuccess: refresh,
  })
  const again = useMutation({ mutationFn: () => api.writeAgain(row.project.id, [row.id]), onSuccess: refresh })

  if (!meta) {
    return (
      <p className="text-sm text-soft">
        The title, description and hashtags are written when the final Short is rendered.
      </p>
    )
  }
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 flex items-baseline justify-between">
          <label className="text-sm font-medium" htmlFor={`title-${row.id}`}>
            Title
          </label>
          <span className={`text-xs tabular-nums ${draft.title.length > 70 ? 'text-accent' : 'text-faint'}`}>
            {draft.title.length}/100
          </span>
        </div>
        <input
          id={`title-${row.id}`}
          className="input"
          maxLength={100}
          value={draft.title}
          onChange={(e) => setDraft({ ...draft, title: e.target.value })}
        />
      </div>
      <div>
        <label className="label" htmlFor={`desc-${row.id}`}>
          Description
        </label>
        <textarea
          id={`desc-${row.id}`}
          className="input min-h-32 py-2 font-mono text-xs leading-relaxed"
          maxLength={5000}
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
        />
        <p className="hint">The credit lines at the top are written by ClipForge, not the AI. Keep them.</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor={`tags-${row.id}`}>
            Hashtags
          </label>
          <input
            id={`tags-${row.id}`}
            className="input"
            value={draft.hashtags}
            onChange={(e) => setDraft({ ...draft, hashtags: e.target.value })}
          />
        </div>
        <div>
          <label className="label" htmlFor={`pin-${row.id}`}>
            Pinned comment
          </label>
          <input
            id={`pin-${row.id}`}
            className="input"
            maxLength={300}
            value={draft.pinned_comment}
            onChange={(e) => setDraft({ ...draft, pinned_comment: e.target.value })}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-primary h-8 px-3" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save text'}
        </button>
        {dirty && (
          <button className="btn h-8 px-3" onClick={() => setDraft(initial)}>
            Undo changes
          </button>
        )}
        <button
          className="btn h-8 px-3"
          disabled={again.isPending}
          onClick={() => again.mutate()}
          title="Ask the AI for new text. Replaces your edits."
        >
          Write again with AI
        </button>
        <span className="text-xs text-faint">
          {meta.edited ? 'Edited by you' : meta.by === 'plain' ? 'Written without the AI' : `Written by ${meta.by}`}
        </span>
      </div>
      {meta.notes?.map((n, i) => (
        <p key={i} className="text-xs text-accent">
          {n}
        </p>
      ))}
      {(save.isError || again.isError) && <ErrorNote>{(save.error ?? again.error)?.message}</ErrorNote>}
    </div>
  )
}

const MARK = { ok: '✓', warn: '!', fail: '✕' } as const
const TONE = { ok: 'text-good', warn: 'text-accent', fail: 'text-bad' } as const

/** The server's Shorts checklist: the same rules the upload and publish steps enforce. */
function Checklist({ list, all }: { list: PublishChecklist; all: boolean }) {
  const shown = all ? list.items : list.items.filter((i) => i.status !== 'ok')
  if (shown.length === 0) return <p className="text-xs text-good">Everything on the Shorts checklist is ready.</p>
  return (
    <ul className="space-y-1 text-xs">
      {shown.map((i) => (
        <li key={i.name} className="flex gap-2">
          <span className={`w-3 shrink-0 text-center font-bold ${TONE[i.status]}`} aria-hidden="true">
            {MARK[i.status]}
          </span>
          <span>
            <span className={i.status === 'ok' ? 'text-soft' : 'text-ink'}>{i.label}</span>
            <span className="block text-faint">{i.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

function YouTubePanel({ row }: { row: ShortRow }) {
  const qc = useQueryClient()
  const yt = useQuery({ queryKey: ['youtube'], queryFn: api.youtube })
  const p = row.project
  const v = row.youtube
  const [confirming, setConfirming] = useState<null | 'public' | 'unlisted'>(null)
  const refresh = () => void qc.invalidateQueries({ queryKey: ['shorts'] })
  const upload = useMutation({ mutationFn: (override: boolean) => api.uploadShorts(p.id, [row.id], override), onSuccess: refresh })
  const publish = useMutation({
    mutationFn: (privacy: 'public' | 'unlisted' | 'private') => api.publishShort(p.id, row.id, privacy, v?.title ?? ''),
    onSuccess: () => {
      setConfirming(null)
      refresh()
    },
  })
  const check = useMutation({ mutationFn: () => api.refreshShort(p.id, row.id), onSuccess: refresh })
  const refreshAll = () => {
    refresh()
    void qc.invalidateQueries({ queryKey: ['schedule'] })
  }
  const [when, setWhen] = useState('')
  const sched = useMutation({
    mutationFn: () => api.scheduleShort(p.id, row.id, when ? new Date(when).toISOString() : undefined),
    onSuccess: refreshAll,
  })
  const unsched = useMutation({ mutationFn: () => api.unscheduleShort(p.id, row.id), onSuccess: refreshAll })
  const list = useQuery({
    queryKey: ['checklist', row.id, row.final?.rendered_at, row.meta, row.youtube, row.review, p.rights_status, yt.data?.connected],
    queryFn: () => api.publishChecklist(p.id, row.id),
    enabled: !!row.final?.exists,
  })
  const sendText = useMutation({
    mutationFn: () => api.sendTextToYouTube(p.id, row.id),
    onSuccess: () => {
      refresh()
      void qc.invalidateQueries({ queryKey: ['checklist', row.id] })
    },
  })
  const failedQc = !!row.final?.qc && !row.final.qc.passed
  const ready = list.data ? (failedQc ? list.data.passed_with_override : list.data.passed) : false
  const err = upload.error ?? publish.error ?? check.error ?? sendText.error ?? sched.error ?? unsched.error
  const goesLive = v?.publish_at ? new Date(v.publish_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : null
  const canSchedule = !v?.privacy || v.privacy === 'private'
  const scheduler = canSchedule && ready && yt.data?.connected && p.rights_status !== 'unverified' && (
    <div className="flex flex-wrap items-center gap-2">
      <button className="btn h-8 px-3" disabled={sched.isPending} onClick={() => sched.mutate()}>
        {when ? 'Schedule at this time' : v?.publish_at ? 'Move to next free slot' : 'Schedule for next free slot'}
      </button>
      <input aria-label="Publish time" type="datetime-local" className="input h-8 w-auto py-0 text-xs" value={when}
        onChange={(e) => setWhen(e.target.value)} />
      {v?.publish_at && (
        <button className="btn h-8 px-3" disabled={unsched.isPending} onClick={() => unsched.mutate()}>Unschedule</button>
      )}
    </div>
  )

  if (!row.final?.exists) return null
  return (
    <div className="rounded-md border border-line px-3 py-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">YouTube</span>
        {goesLive && <Badge tone="good">Goes public {goesLive}</Badge>}
        {v?.video_id ? (
          <Badge tone={v.privacy === 'public' ? 'good' : v.privacy === 'unlisted' ? 'info' : 'warn'}>
            {v.privacy === 'public' ? 'Public' : v.privacy === 'unlisted' ? 'Unlisted' : 'Private'} on {v.channel ?? 'your channel'}
          </Badge>
        ) : v?.state === 'uploading' ? (
          <Badge tone="info">Upload started</Badge>
        ) : v?.state === 'scheduled' ? (
          <Badge tone="info">Scheduled · uploading</Badge>
        ) : (
          <Badge>Not uploaded</Badge>
        )}
      </div>

      {!v?.video_id ? (
        <>
          {p.rights_status === 'unverified' ? (
            <p className="text-xs text-bad">
              Not uploaded: the rights to this video are not confirmed. Record ownership or the creator's permission
              on the project first.
            </p>
          ) : !yt.data?.connected ? (
            <p className="text-xs text-soft">
              Connect your channel in{' '}
              <Link to="/settings" className="text-accent hover:underline">
                Settings
              </Link>{' '}
              to upload.
            </p>
          ) : (
            <div className="space-y-2">
              {list.data && <Checklist list={list.data} all={false} />}
              <div className="flex flex-wrap items-center gap-2">
                <button
                  className={`btn h-8 px-3 ${failedQc ? 'btn-danger' : 'btn-primary'}`}
                  disabled={upload.isPending || !ready}
                  onClick={() => upload.mutate(failedQc)}
                  title={failedQc ? 'Upload even though a quality check failed' : undefined}
                >
                  {failedQc ? 'Upload as private anyway' : 'Upload as private'}
                </button>
                <span className="text-xs text-faint">Nobody can see it until you publish it.</span>
              </div>
              {scheduler}
            </div>
          )}
        </>
      ) : (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <a className="btn h-8 px-3" href={v.url} target="_blank" rel="noreferrer">
              Open on YouTube
            </a>
            <a className="btn h-8 px-3" href={v.studio_url} target="_blank" rel="noreferrer">
              Open in Studio
            </a>
            <button className="btn h-8 px-3" disabled={check.isPending} onClick={() => check.mutate()}>
              Check status
            </button>
            {v.privacy !== 'public' && (
              <button className="btn btn-primary h-8 px-3" onClick={() => setConfirming('public')}>
                Publish…
              </button>
            )}
            {v.privacy !== 'private' && (
              <button className="btn h-8 px-3" disabled={publish.isPending} onClick={() => publish.mutate('private')}>
                Make private again
              </button>
            )}
          </div>
          {list.data?.text_differs && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-accent/30 bg-accent/5 px-2 py-1.5">
              <span className="text-xs text-accent">The title or description was edited after the upload.</span>
              <button className="btn h-7 px-2 text-xs" disabled={sendText.isPending} onClick={() => sendText.mutate()}>
                {sendText.isPending ? 'Sending…' : 'Send edited text to YouTube'}
              </button>
            </div>
          )}
          {v.note && <p className="text-xs text-accent">{v.note}</p>}
          {v.schedule_note && <p className="text-xs text-accent">{v.schedule_note}</p>}
          {v.privacy === 'private' && scheduler}
          {(v.rejection || v.failure) && <p className="text-xs text-bad">YouTube: {v.rejection ?? v.failure}</p>}
          {row.meta?.pinned_comment && (
            <p className="text-xs text-soft">
              Pinned comment (post and pin it yourself; YouTube's API cannot pin):{' '}
              <span className="text-ink">{row.meta.pinned_comment}</span>
            </p>
          )}
        </div>
      )}

      {confirming && v?.video_id && (
        <div className="mt-3 rounded-md border border-accent/40 bg-accent/5 p-3">
          <p className="mb-2 text-sm font-medium">YouTube Shorts checklist</p>
          {list.isPending ? <Spinner /> : list.data && <Checklist list={list.data} all />}
          {list.data?.text_differs && (
            <p className="mt-2 text-xs text-accent">Send the edited text to YouTube first, or the old text goes public.</p>
          )}
          <p className="mt-3 text-sm">
            Make “<span className="font-medium">{v.title}</span>” visible on {v.channel ?? 'your channel'}?
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select
              aria-label="Visibility"
              className="input h-8 w-auto py-0 text-xs"
              value={confirming}
              onChange={(e) => setConfirming(e.target.value as 'public' | 'unlisted')}
            >
              <option value="public">Public: anyone can find it</option>
              <option value="unlisted">Unlisted: only people with the link</option>
            </select>
            <button
              className="btn btn-primary h-8 px-3"
              disabled={publish.isPending || !ready || !!list.data?.text_differs}
              onClick={() => publish.mutate(confirming)}
            >
              {publish.isPending ? 'Publishing…' : `Yes, make it ${confirming}`}
            </button>
            <button className="btn h-8 px-3" onClick={() => setConfirming(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {err && (
        <div className="mt-2">
          <ErrorNote>{err.message}</ErrorNote>
        </div>
      )}
    </div>
  )
}

function ShortCard({ row, onExported }: { row: ShortRow; onExported: (r: ExportResult) => void }) {
  const qc = useQueryClient()
  const refresh = () => void qc.invalidateQueries({ queryKey: ['shorts'] })
  const p = row.project
  const q = row.final?.qc
  const review = useMutation({
    mutationFn: (v: 'approved' | 'rejected') => api.reviewClip(p.id, row.id, v, !row.valid),
    onSuccess: refresh,
  })
  const render = useMutation({ mutationFn: () => api.renderFinal(p.id, [row.id]), onSuccess: refresh })
  const exp = useMutation({
    mutationFn: (override: boolean) => api.exportShorts(p.id, [row.id], override),
    onSuccess: (r) => {
      onExported(r)
      refresh()
    },
  })
  const failed = q && !q.passed

  return (
    <li className={`rounded-lg border p-4 ${failed ? 'border-bad/30' : row.exported ? 'border-good/40' : 'border-line'}`}>
      <div className="grid gap-5 md:grid-cols-[200px_1fr]">
        <div>
          {row.final?.exists ? (
            <video
              className="aspect-[9/16] w-full rounded-md bg-black"
              controls
              preload="none"
              src={`/api/projects/${p.id}/clips/${row.id}/final?v=${encodeURIComponent(row.final.rendered_at)}`}
            />
          ) : row.has_vertical ? (
            <video
              className="aspect-[9/16] w-full rounded-md bg-black opacity-80"
              controls
              preload="none"
              src={`/api/projects/${p.id}/clips/${row.id}/vertical`}
            />
          ) : (
            <div className="grid aspect-[9/16] w-full place-items-center rounded-md bg-raised text-xs text-faint">
              No preview
            </div>
          )}
          <p className="mt-1.5 text-[11px] text-faint">
            {row.final?.exists
              ? `Final · ${row.final.width}×${row.final.height} · ${(row.final.size_bytes / 1e6).toFixed(1)} MB`
              : 'Preview · not rendered yet'}
          </p>
        </div>

        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">
              Clip {row.rank}
              {row.parts > 1 && ` · Part ${row.part} of ${row.parts}`}
            </span>
            {row.category && <Badge tone="info">{CATEGORY_LABEL[row.category] ?? row.category}</Badge>}
            {row.score != null && <Badge>Viral {row.score}</Badge>}
            <ScoreBadge q={q} />
            {row.review === 'approved' ? (
              <Badge tone="good">Approved</Badge>
            ) : row.review === 'rejected' ? (
              <Badge>Rejected</Badge>
            ) : (
              <Badge tone="warn">Waiting for review</Badge>
            )}
            {row.exported && <Badge tone="good">{row.exported.anyway ? 'Exported anyway' : 'Exported'}</Badge>}
            {p.rights_status === 'unverified' && <Badge tone="bad">Rights not confirmed</Badge>}
          </div>

          {q && <QualityList q={q} />}

          <TextEditor row={row} />

          <YouTubePanel row={row} />

          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
            {row.review !== 'approved' && (
              <button className="btn h-8 px-3" disabled={review.isPending} onClick={() => review.mutate('approved')}>
                {row.valid ? 'Approve' : 'Approve anyway'}
              </button>
            )}
            {row.review !== 'rejected' && (
              <button className="btn h-8 px-3" disabled={review.isPending} onClick={() => review.mutate('rejected')}>
                Reject
              </button>
            )}
            {row.review === 'approved' && (
              <button className="btn h-8 px-3" disabled={render.isPending} onClick={() => render.mutate()}>
                {row.final ? 'Render again' : 'Render final'}
              </button>
            )}
            {row.final?.exists && !failed && (
              <button
                className="btn btn-primary h-8 px-3"
                disabled={exp.isPending || row.review !== 'approved'}
                onClick={() => exp.mutate(false)}
              >
                {row.exported ? 'Export again' : 'Export'}
              </button>
            )}
            {row.final?.exists && failed && row.review === 'approved' && (
              <button
                className="btn btn-danger h-8 px-3"
                disabled={exp.isPending}
                onClick={() => exp.mutate(true)}
                title="Export even though a quality check failed"
              >
                Export anyway
              </button>
            )}
            {row.final?.exists && (
              <a className="btn h-8 px-3" href={`/api/projects/${p.id}/clips/${row.id}/final?download=1`}>
                Download
              </a>
            )}
            <Link to={`/editor?project=${p.id}&clip=${row.id}`} className="ml-auto text-xs text-soft hover:text-ink">
              Framing, captions and music
            </Link>
          </div>
          {row.export_blocker && row.review === 'approved' && row.final?.exists && !failed && (
            <p className="text-xs text-soft">{row.export_blocker}</p>
          )}
          {(review.isError || render.isError || exp.isError) && (
            <ErrorNote>{(review.error ?? render.error ?? exp.error)?.message}</ErrorNote>
          )}
        </div>
      </div>
    </li>
  )
}

function ProjectGroup({ rows }: { rows: ShortRow[] }) {
  const qc = useQueryClient()
  const p = rows[0].project
  const [result, setResult] = useState<ExportResult | null>(null)
  const ready = rows.filter((r) => r.review === 'approved' && r.final?.exists && r.final.qc?.passed !== false)
  const exportAll = useMutation({
    mutationFn: () => api.exportShorts(p.id),
    onSuccess: (r) => {
      setResult(r)
      void qc.invalidateQueries({ queryKey: ['shorts'] })
    },
  })
  const open = useMutation({ mutationFn: () => api.openExports(p.id) })

  return (
    <Section
      title={p.title}
      aside={
        <span className="flex flex-wrap items-center gap-2">
          <Badge tone={RIGHTS[p.rights_status]?.tone}>{RIGHTS[p.rights_status]?.label ?? p.rights_status}</Badge>
          <button
            className="btn btn-primary h-8 px-3"
            disabled={ready.length === 0 || exportAll.isPending}
            onClick={() => exportAll.mutate()}
          >
            {exportAll.isPending ? 'Exporting…' : `Export ${ready.length} ready`}
          </button>
          <button className="btn h-8 px-3" onClick={() => open.mutate()}>
            Open folder
          </button>
        </span>
      }
    >
      {p.rights_status === 'unverified' && (
        <p className="mb-4 rounded-md border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">
          The rights to this video are not confirmed. Shorts can be exported, marked as such, but will not be published.
        </p>
      )}
      {result && (
        <div className="mb-4 rounded-md border border-line px-3 py-2 text-sm">
          <p className={result.exported.length ? 'text-good' : 'text-soft'}>
            {result.exported.length} Short(s) exported to <span className="break-all font-mono text-xs">{result.folder}</span>
          </p>
          {result.refused.map((r) => (
            <p key={r.clip_id} className="text-xs text-bad">
              Clip {r.rank}
              {r.part > 1 ? ` part ${r.part}` : ''} not exported: {r.reason}
            </p>
          ))}
        </div>
      )}
      {exportAll.isError && <ErrorNote>{exportAll.error.message}</ErrorNote>}
      <ol className="space-y-3">
        {rows.map((r) => (
          <ShortCard key={r.id} row={r} onExported={setResult} />
        ))}
      </ol>
    </Section>
  )
}

export default function Shorts() {
  const shorts = useQuery({ queryKey: ['shorts'], queryFn: api.shorts })
  const groups = useMemo(() => {
    const by = new Map<number, ShortRow[]>()
    for (const r of shorts.data ?? []) by.set(r.project.id, [...(by.get(r.project.id) ?? []), r])
    return [...by.values()]
  }, [shorts.data])

  return (
    <>
      <PageHeader
        title="Generated Shorts"
        subtitle="Approved and rendered Shorts: quality checks, title, description and hashtags, and export."
      />
      <ScheduleCard />
      {shorts.isPending ? (
        <Spinner />
      ) : shorts.isError ? (
        <ErrorNote>{shorts.error.message}</ErrorNote>
      ) : groups.length === 0 ? (
        <Empty title="No Shorts yet">
          Approve clips in a project, then render them as final Shorts. They appear here with their quality checks
          and text, ready to export.{' '}
          <Link to="/" className="text-accent hover:underline">
            Go to the dashboard
          </Link>
        </Empty>
      ) : (
        <div className="space-y-6">
          {groups.map((rows) => (
            <ProjectGroup key={rows[0].project.id} rows={rows} />
          ))}
        </div>
      )}
    </>
  )
}

function ScheduleCard() {
  const qc = useQueryClient()
  const status = useQuery({ queryKey: ['schedule'], queryFn: api.schedule, refetchInterval: 60_000 })
  const [times, setTimes] = useState('')
  const s = status.data
  useEffect(() => {
    if (s) setTimes(s.settings.times.join(', '))
  }, [s?.settings.times.join(',')])
  const save = useMutation({
    mutationFn: (v: Partial<NonNullable<typeof s>['settings']>) => api.putSchedule(v),
    onSuccess: (d) => qc.setQueryData(['schedule'], d),
  })
  if (!s) return status.isError ? <ErrorNote>{status.error.message}</ErrorNote> : null
  const fmt = (iso: string) => new Date(iso).toLocaleString(undefined, { weekday: 'short', dateStyle: 'medium', timeStyle: 'short' })
  return (
    <div className="card mb-6 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">Posting schedule</h2>
        <Badge tone={s.settings.enabled ? 'good' : 'neutral'}>{s.settings.enabled ? 'On' : 'Off'}</Badge>
      </div>
      <p className="mt-1 text-xs text-soft">
        Shorts are uploaded as private with a publish time, and YouTube makes them public at that moment, even when
        this computer is off. Approving a Short with the schedule on is your OK to publish it at its slot.
      </p>
      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <label className="label" htmlFor="sched-times">Posting times (24-hour)</label>
          <input id="sched-times" className="input" value={times} onChange={(e) => setTimes(e.target.value)}
            placeholder="12:00, 18:00" />
          <p className="hint">Time zone: {s.settings.timezone}. At most {s.max_per_day} a day.</p>
        </div>
        <label className="flex items-start gap-2 pt-6 text-sm">
          <input type="checkbox" className="mt-0.5 accent-accent" checked={s.settings.enabled}
            onChange={(e) => save.mutate({ enabled: e.target.checked })} />
          <span>Schedule is on</span>
        </label>
        <label className="flex items-start gap-2 pt-6 text-sm">
          <input type="checkbox" className="mt-0.5 accent-accent" checked={s.settings.auto}
            onChange={(e) => save.mutate({ auto: e.target.checked })} />
          <span>
            Schedule approved Shorts automatically
            <span className="hint block">Fills the free slots of the next {s.horizon_hours} hours.</span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm sm:col-span-3">
          <input type="checkbox" className="mt-0.5 accent-accent" checked={s.settings.require_story}
            onChange={(e) => save.mutate({ require_story: e.target.checked })} />
          <span>
            Wait for my narration
            <span className="hint block">Automatic mode only schedules a Short of someone else's stream once its final
              render has at least 4 s of your recorded Story lines.</span>
          </span>
        </label>
      </div>
      <div className="mt-3 flex gap-2">
        <button className="btn" disabled={save.isPending}
          onClick={() => save.mutate({ times: times.split(/[,\s]+/).filter(Boolean) })}>Save times</button>
      </div>
      {save.error && <div className="mt-3"><ErrorNote>{save.error.message}</ErrorNote></div>}
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <div className="text-xs font-medium text-soft">Coming up</div>
          {s.upcoming.filter((u) => !u.past).length === 0 ? (
            <p className="mt-1 text-sm text-faint">Nothing scheduled.</p>
          ) : (
            <ul className="mt-1 space-y-1 text-sm">
              {s.upcoming.filter((u) => !u.past).map((u) => (
                <li key={u.clip_id}><span className="text-ink">{fmt(u.publish_at)}</span> · {u.title ?? u.project}</li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <div className="text-xs font-medium text-soft">Next free slots</div>
          <ul className="mt-1 space-y-1 text-sm text-faint">
            {s.next_slots.map((n) => <li key={n}>{fmt(n)}</li>)}
          </ul>
        </div>
      </div>
    </div>
  )
}
