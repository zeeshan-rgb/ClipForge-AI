import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, type Clip, type Project, type Story, type StoryLine } from '../api'
import { Badge, ErrorNote, Section, Spinner } from '../ui'

const ROLE_LABEL: Record<StoryLine['role'], string> = {
  hook: 'Hook (first seconds)',
  context: 'Context',
  reaction: 'Your reaction',
  outro: 'Outro',
}

/** One line: its words, a record button using the browser's microphone, and playback. */
function LineRow({
  project, clip, line, onText, onSaved,
}: {
  project: Project
  clip: Clip
  line: StoryLine
  onText: (text: string) => void
  onSaved: (s: Story) => void
}) {
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const rec = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const upload = useMutation({
    mutationFn: (blob: Blob) => api.uploadStoryTake(project.id, clip.id, line.id, blob),
    onSuccess: onSaved,
  })
  const remove = useMutation({ mutationFn: () => api.deleteStoryTake(project.id, clip.id, line.id), onSuccess: onSaved })

  const start = async () => {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      const r = new MediaRecorder(stream)
      chunks.current = []
      r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data)
      r.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        upload.mutate(new Blob(chunks.current, { type: r.mimeType || 'audio/webm' }))
      }
      r.start()
      rec.current = r
      setRecording(true)
    } catch {
      setError('The microphone could not be opened. Allow microphone access for this page and try again.')
    }
  }
  const stop = () => {
    rec.current?.stop()
    setRecording(false)
  }

  const took = line.audio ? `${line.seconds?.toFixed(1)} s` : null
  return (
    <div className="rounded-md border border-line p-3">
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-medium text-soft">{ROLE_LABEL[line.role]}</span>
        <span className="text-faint">at {line.at?.toFixed(1)} s{took ? ` · recorded ${took}` : ''}</span>
      </div>
      <textarea
        aria-label={`${ROLE_LABEL[line.role]} text`}
        className="input min-h-14 text-sm"
        value={line.text}
        onChange={(e) => onText(e.target.value)}
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {recording ? (
          <button className="btn btn-danger h-8 px-3" onClick={stop}>■ Stop</button>
        ) : (
          <button className="btn btn-primary h-8 px-3" disabled={upload.isPending} onClick={() => void start()}>
            ● {line.audio ? 'Record again' : 'Record'}
          </button>
        )}
        {upload.isPending && <span className="text-xs text-soft">Cleaning the recording…</span>}
        {line.audio && !recording && (
          <>
            <audio controls className="h-8" src={api.storyTakeUrl(project.id, clip.id, line.id, line.seconds)} />
            <button className="btn h-8 px-2 text-xs" onClick={() => remove.mutate()}>Delete take</button>
          </>
        )}
      </div>
      {(error || upload.error || remove.error) && (
        <p className="mt-2 text-xs text-bad">{error ?? (upload.error ?? remove.error)?.message}</p>
      )}
    </div>
  )
}

export function StoryPanel({ project, clip, busy }: { project: Project; clip: Clip; busy: boolean }) {
  const qc = useQueryClient()
  const key = ['story', project.id, clip.id]
  const q = useQuery({ queryKey: key, queryFn: () => api.story(project.id, clip.id) })
  const [edit, setEdit] = useState<Story | null>(null)
  useEffect(() => setEdit(q.data ?? null), [q.data])
  const set = (s: Story) => {
    qc.setQueryData(key, s)
    void qc.invalidateQueries({ queryKey: ['clips', project.id] })
  }
  const draft = useMutation({ mutationFn: () => api.draftStory(project.id, clip.id), onSuccess: set })
  const save = useMutation({
    mutationFn: (s: Story) =>
      api.saveStory(project.id, clip.id, {
        lines: s.lines.map((l) => ({ id: l.id, role: l.role, text: l.text, at: l.auto_at ? null : l.at })),
        cards: s.cards.map((c) => ({ id: c.id, text: c.text, at: c.at, seconds: c.seconds })),
        stickers: s.stickers.map((x) => ({ id: x.id, sticker: x.sticker, at: x.at, seconds: x.seconds })),
      }),
    onSuccess: set,
  })
  const render = useMutation({
    mutationFn: () => api.renderFinal(project.id, [clip.id]),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['jobs', project.id] }),
  })

  if (q.isPending || !edit) return <Section title="Story mode"><Spinner /></Section>
  const s = edit
  const c = s.contribution
  const dirty = JSON.stringify(s) !== JSON.stringify(q.data)
  const err = draft.error ?? save.error ?? render.error

  return (
    <Section
      title="Story mode: your narration"
      aside={
        c.seconds >= 4 ? <Badge tone="good">{c.seconds} s of your voice</Badge>
          : c.seconds > 0 ? <Badge tone="warn">Only {c.seconds} s</Badge>
          : <Badge tone="warn">No narration yet</Badge>
      }
    >
      <p className="mb-3 text-xs leading-relaxed text-soft">
        Your own commentary is what makes a clip of someone else's stream yours. ClipForge suggests the lines; change
        them to your words, then record each one. The stream's sound dips while you speak.
      </p>
      <div className="mb-3 flex flex-wrap gap-2">
        <button className="btn h-8 px-3" disabled={draft.isPending} onClick={() => draft.mutate()}>
          {draft.isPending ? 'Writing…' : s.lines.length ? 'Suggest new lines' : 'Suggest lines'}
        </button>
        {dirty && (
          <button className="btn btn-primary h-8 px-3" disabled={save.isPending} onClick={() => save.mutate(s)}>
            Save changes
          </button>
        )}
      </div>

      {s.lines.length > 0 && (
        <div className="space-y-2">
          {s.lines.map((line, n) => (
            <LineRow
              key={line.id}
              project={project}
              clip={clip}
              line={line}
              onSaved={set}
              onText={(text) => setEdit({ ...s, lines: s.lines.map((l, k) => (k === n ? { ...l, text } : l)) })}
            />
          ))}
          {dirty && <p className="text-xs text-accent">Save your changes before recording a changed line.</p>}
        </div>
      )}

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-medium text-soft">Story cards</div>
          {s.cards.map((card, n) => (
            <div key={card.id} className="mb-1.5 flex gap-1.5">
              <input aria-label="Card text" className="input h-8 text-xs" value={card.text} maxLength={40}
                onChange={(e) => setEdit({ ...s, cards: s.cards.map((x, k) => (k === n ? { ...x, text: e.target.value } : x)) })} />
              <input aria-label="Card time" type="number" step="0.1" className="input h-8 w-20 text-xs" value={card.at}
                onChange={(e) => setEdit({ ...s, cards: s.cards.map((x, k) => (k === n ? { ...x, at: Number(e.target.value) } : x)) })} />
              <button className="btn h-8 px-2 text-xs" onClick={() => setEdit({ ...s, cards: s.cards.filter((_, k) => k !== n) })}>✕</button>
            </div>
          ))}
          {s.cards.length < 3 && (
            <button className="btn h-7 px-2 text-xs"
              onClick={() => setEdit({ ...s, cards: [...s.cards, { id: '', text: '10 minutes earlier…', at: 0.3, seconds: 2.2 }] })}>
              + Card
            </button>
          )}
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-soft">Reaction stickers</div>
          {s.stickers.map((x, n) => (
            <div key={x.id || n} className="mb-1.5 flex gap-1.5">
              <select aria-label="Sticker" className="input h-8 text-xs" value={x.sticker}
                onChange={(e) => setEdit({ ...s, stickers: s.stickers.map((y, k) => (k === n ? { ...y, sticker: e.target.value } : y)) })}>
                {Object.entries(s.stickers_available).map(([id, v]) => <option key={id} value={id}>{v.text}</option>)}
              </select>
              <input aria-label="Sticker time" type="number" step="0.1" className="input h-8 w-20 text-xs" value={x.at}
                onChange={(e) => setEdit({ ...s, stickers: s.stickers.map((y, k) => (k === n ? { ...y, at: Number(e.target.value) } : y)) })} />
              <button className="btn h-8 px-2 text-xs" onClick={() => setEdit({ ...s, stickers: s.stickers.filter((_, k) => k !== n) })}>✕</button>
            </div>
          ))}
          {s.stickers.length < 2 && (
            <button className="btn h-7 px-2 text-xs"
              onClick={() => setEdit({ ...s, stickers: [...s.stickers, { id: '', sticker: 'no_way', at: 1, seconds: 1.6 }] })}>
              + Sticker
            </button>
          )}
        </div>
      </div>
      <p className="hint mt-2">Stickers are drawn by ClipForge as text, so there is nothing to license.</p>

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <button className="btn btn-primary h-8 px-3" disabled={busy || render.isPending || dirty} onClick={() => render.mutate()}>
          Render the final Short with this story
        </button>
        {q.data?.render_needed && <span className="text-xs text-accent">The story changed since the last render.</span>}
        {q.data?.in_final && !q.data.render_needed && (
          <span className="text-xs text-good">In the final Short: {q.data.in_final.seconds} s of narration.</span>
        )}
      </div>
      {err && <div className="mt-3"><ErrorNote>{err.message}</ErrorNote></div>}
    </Section>
  )
}
