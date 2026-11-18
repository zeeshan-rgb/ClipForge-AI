import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api, type MusicMeta, type Track, type TrackFields } from '../api'
import { duration as clock } from '../format'
import { Badge, Empty, ErrorNote, Section, Spinner } from '../ui'

interface Draft {
  title: string
  moods: string[]
  source: string
  licence_name: string
  licence_url: string
  licence_note: string
  attribution: string
}

const BLANK: Draft = {
  title: '',
  moods: [],
  source: '',
  licence_name: '',
  licence_url: '',
  licence_note: '',
  attribution: '',
}

function fromTrack(t: Track): Draft {
  return {
    title: t.title,
    moods: t.moods,
    source: t.source ?? '',
    licence_name: t.licence_name ?? '',
    licence_url: t.licence_url ?? '',
    licence_note: t.licence_note ?? '',
    attribution: t.attribution ?? '',
  }
}

/** What is still missing, in the same words the server uses. The server is the authority. */
function missing(d: Draft, meta: MusicMeta): string[] {
  const out: string[] = []
  if (!d.source.trim()) out.push('where the track came from')
  if (!d.licence_name.trim()) out.push('the licence')
  if (!d.licence_url.trim() && !d.licence_note.trim()) out.push('a licence link or a note that shows you may use it')
  const needsCredit = meta.licences.find((l) => l.name === d.licence_name)?.requires_attribution ?? /\bBY\b/i.test(d.licence_name)
  if (d.licence_name.trim() && needsCredit && !d.attribution.trim()) out.push('the credit line')
  return out
}

function TrackFieldsForm({
  draft,
  onChange,
  meta,
  idPrefix,
}: {
  draft: Draft
  onChange: (d: Draft) => void
  meta: MusicMeta
  idPrefix: string
}) {
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => onChange({ ...draft, [k]: v })
  const needsCredit = meta.licences.find((l) => l.name === draft.licence_name)?.requires_attribution ?? /\bBY\b/i.test(draft.licence_name)
  const toggle = (m: string) =>
    set('moods', draft.moods.includes(m) ? draft.moods.filter((x) => x !== m) : [...draft.moods, m])
  return (
    <div className="space-y-4">
      <div>
        <label className="label" htmlFor={`${idPrefix}-title`}>
          Title
        </label>
        <input id={`${idPrefix}-title`} className="input" value={draft.title} maxLength={200} onChange={(e) => set('title', e.target.value)} />
      </div>
      <fieldset>
        <legend className="mb-1.5 text-sm font-medium">What it sounds like</legend>
        <div className="flex flex-wrap gap-1.5">
          {meta.moods.map((m) => (
            <button
              key={m.name}
              type="button"
              aria-pressed={draft.moods.includes(m.name)}
              onClick={() => toggle(m.name)}
              className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs ${
                draft.moods.includes(m.name) ? 'border-accent bg-accent/15 text-accent' : 'border-line text-soft hover:text-ink'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <p className="hint">A track is chosen automatically only for moments that match its mood. It can always be picked by hand.</p>
      </fieldset>

      <div className="rounded-lg border border-line p-4">
        <div className="mb-3 text-sm font-medium">Licence</div>
        <p className="mb-4 text-xs leading-relaxed text-soft">
          ClipForge uses a track only when all of this is filled in, so no track is used without you knowing you may
          use it.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor={`${idPrefix}-source`}>
              Where it came from
            </label>
            <input
              id={`${idPrefix}-source`}
              className="input"
              placeholder="Website, shop, or library name"
              value={draft.source}
              maxLength={500}
              onChange={(e) => set('source', e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor={`${idPrefix}-licence`}>
              Licence
            </label>
            <select
              id={`${idPrefix}-licence`}
              className="input"
              value={draft.licence_name}
              onChange={(e) => set('licence_name', e.target.value)}
            >
              <option value="">Choose…</option>
              {meta.licences.map((l) => (
                <option key={l.name} value={l.name}>
                  {l.name}
                </option>
              ))}
              {draft.licence_name && !meta.licences.some((l) => l.name === draft.licence_name) && (
                <option value={draft.licence_name}>{draft.licence_name}</option>
              )}
            </select>
          </div>
          <div>
            <label className="label" htmlFor={`${idPrefix}-url`}>
              Licence link
            </label>
            <input
              id={`${idPrefix}-url`}
              className="input"
              placeholder="https://…"
              value={draft.licence_url}
              maxLength={500}
              onChange={(e) => set('licence_url', e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor={`${idPrefix}-note`}>
              …or a note <span className="font-normal text-faint">(receipt number, where you downloaded it)</span>
            </label>
            <input
              id={`${idPrefix}-note`}
              className="input"
              value={draft.licence_note}
              maxLength={2000}
              onChange={(e) => set('licence_note', e.target.value)}
            />
          </div>
          {needsCredit && (
            <div className="sm:col-span-2">
              <label className="label" htmlFor={`${idPrefix}-credit`}>
                Credit line <span className="font-normal text-faint">(this licence requires it)</span>
              </label>
              <input
                id={`${idPrefix}-credit`}
                className="input"
                placeholder="Music by … (licence)"
                value={draft.attribution}
                maxLength={500}
                onChange={(e) => set('attribution', e.target.value)}
              />
              <p className="hint">It is added to the description of every Short that uses this track.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function toFields(d: Draft): TrackFields {
  return {
    title: d.title.trim() || undefined,
    moods: d.moods,
    source: d.source.trim(),
    licence_name: d.licence_name.trim(),
    licence_url: d.licence_url.trim(),
    licence_note: d.licence_note.trim(),
    attribution: d.attribution.trim(),
  }
}

function AddTrack({ meta, onDone }: { meta: MusicMeta; onDone: () => void }) {
  const qc = useQueryClient()
  const [path, setPath] = useState('')
  const [draft, setDraft] = useState<Draft>(BLANK)
  const pick = useMutation({
    mutationFn: api.pickAudio,
    onSuccess: (r) => r.path && setPath(r.path),
  })
  const add = useMutation({
    mutationFn: () => api.importTrack({ path: path.trim(), ...toFields(draft) }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['tracks'] })
      onDone()
    },
  })
  const still = missing(draft, meta)

  return (
    <div className="rounded-lg border border-line bg-bg p-5">
      <div className="mb-4">
        <label className="label" htmlFor="track-path">
          Audio file
        </label>
        <div className="flex gap-2">
          <input
            id="track-path"
            className="input"
            placeholder="D:\Music\track.mp3"
            value={path}
            onChange={(e) => setPath(e.target.value)}
          />
          <button type="button" className="btn h-10 shrink-0" onClick={() => pick.mutate()} disabled={pick.isPending}>
            {pick.isPending ? 'Choosing…' : 'Browse…'}
          </button>
        </div>
        {pick.isError && <p className="mt-1.5 text-xs text-bad">{pick.error.message}</p>}
        <p className="hint">The file is copied into ClipForge’s own music folder and analysed (length, level, tempo, beats). Supported: {meta.audio_extensions.join(' ')}</p>
      </div>
      <TrackFieldsForm draft={draft} onChange={setDraft} meta={meta} idPrefix="add" />
      {add.isError && (
        <div className="mt-4">
          <ErrorNote>{add.error.message}</ErrorNote>
        </div>
      )}
      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button className="btn btn-primary" disabled={path.trim() === '' || add.isPending} onClick={() => add.mutate()}>
          {add.isPending ? 'Adding and analysing…' : 'Add track'}
        </button>
        <button className="btn" onClick={onDone} disabled={add.isPending}>
          Cancel
        </button>
        {still.length > 0 && (
          <span className="text-sm text-accent">
            It can be added now but not used until you add {still.join(', ')}.
          </span>
        )}
      </div>
    </div>
  )
}

function TrackCard({ track, meta }: { track: Track; meta: MusicMeta }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Draft>(fromTrack(track))
  const [confirm, setConfirm] = useState(false)
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['tracks'] })
    void qc.invalidateQueries({ queryKey: ['clips'] })
  }
  const save = useMutation({
    mutationFn: () => api.updateTrack(track.id, toFields(draft)),
    onSuccess: () => {
      refresh()
      setEditing(false)
    },
  })
  const remove = useMutation({
    mutationFn: (force: boolean) => api.deleteTrack(track.id, force),
    onSuccess: refresh,
  })
  const inUse = remove.error instanceof ApiError && remove.error.status === 409

  return (
    <li className={`rounded-lg border p-4 ${track.usable ? 'border-line' : 'border-bad/30 bg-bad/5'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{track.title}</span>
        {track.usable ? <Badge tone="good">Licensed · {track.licence_name}</Badge> : <Badge tone="bad">Cannot be used yet</Badge>}
        {track.moods.length === 0 ? (
          <Badge tone="warn">No mood: only chosen by hand</Badge>
        ) : (
          track.moods.map((m) => <Badge key={m} tone="info">{meta.moods.find((x) => x.name === m)?.label ?? m}</Badge>)
        )}
      </div>
      <p className="mt-1.5 text-xs text-faint">
        {clock(track.duration)} ·{' '}
        {track.steady_beat ? `${track.bpm} beats a minute, steady beat` : 'no steady beat, so it starts from the beginning'}
        {track.used_by > 0 && ` · used by ${track.used_by} clip(s)`}
      </p>
      {!track.usable && (
        <ul className="mt-2 list-disc pl-5 text-sm text-bad">
          {track.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {track.warnings.map((w) => (
        <p key={w} className="mt-2 text-sm text-accent">
          {w}
        </p>
      ))}
      {track.usable && track.requires_attribution && (
        <p className="mt-2 text-xs text-soft">Credit added to descriptions: “{track.attribution}”</p>
      )}
      <audio className="mt-3 h-9 w-full max-w-md" controls preload="none" src={`/api/music/${track.id}/audio`} />

      {editing ? (
        <div className="mt-4">
          <TrackFieldsForm draft={draft} onChange={setDraft} meta={meta} idPrefix={`edit-${track.id}`} />
          {save.isError && (
            <div className="mt-3">
              <ErrorNote>{save.error.message}</ErrorNote>
            </div>
          )}
          <div className="mt-4 flex gap-2">
            <button className="btn btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
            <button className="btn" onClick={() => { setEditing(false); setDraft(fromTrack(track)) }}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button className="text-xs text-soft hover:text-ink" onClick={() => { setDraft(fromTrack(track)); setEditing(true) }}>
            {track.usable ? 'Edit' : 'Add licence details'}
          </button>
          {confirm ? (
            <>
              <span className="text-xs text-soft">Remove this track?</span>
              <button className="text-xs text-bad hover:underline" onClick={() => remove.mutate(false)} disabled={remove.isPending}>
                Yes, remove
              </button>
              <button className="text-xs text-soft hover:text-ink" onClick={() => { setConfirm(false); remove.reset() }}>
                Keep
              </button>
            </>
          ) : (
            <button className="text-xs text-soft hover:text-ink" onClick={() => setConfirm(true)}>
              Remove
            </button>
          )}
        </div>
      )}
      {remove.isError && (
        <p className="mt-2 text-sm text-bad">
          {remove.error.message}{' '}
          {inUse && (
            <button className="underline" onClick={() => remove.mutate(true)}>
              Remove anyway
            </button>
          )}
        </p>
      )}
    </li>
  )
}

function AudioLibraryImport() {
  const qc = useQueryClient()
  const [folder, setFolder] = useState('')
  const run = useMutation({
    mutationFn: () => api.importMusicFolder(folder.trim()),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['tracks'] }),
  })
  return (
    <div className="mb-5 rounded-md border border-line p-3">
      <div className="text-sm font-medium">Add a folder from the YouTube Audio Library</div>
      <p className="hint mt-1">
        In YouTube Studio → Audio Library, download the tracks you like into one folder (skip ones marked “Attribution
        required”, or add their credit after). ClipForge records the Audio Library licence, guesses each track's mood
        from its tempo and energy, and then picks a fitting track for each Short. Correct any mood below.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <input aria-label="Folder" className="input min-w-0 flex-1" placeholder="/Users/you/Downloads/Audio Library"
          value={folder} onChange={(e) => setFolder(e.target.value)} />
        <button className="btn btn-primary" disabled={!folder.trim() || run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? 'Analysing…' : 'Import folder'}
        </button>
      </div>
      {run.data && (
        <p className="mt-2 text-xs text-soft">
          Added {run.data.added.length}
          {run.data.skipped.length ? `, skipped ${run.data.skipped.length} (${run.data.skipped.slice(0, 3).map((x) => `${x.file}: ${x.reason}`).join('; ')})` : ''}.
        </p>
      )}
      {run.error && <div className="mt-2"><ErrorNote>{run.error.message}</ErrorNote></div>}
    </div>
  )
}

export function MusicLibrary() {
  const tracks = useQuery({ queryKey: ['tracks'], queryFn: api.tracks })
  const meta = useQuery({ queryKey: ['music-meta'], queryFn: api.musicMeta, staleTime: Infinity })
  const [adding, setAdding] = useState(false)

  return (
    <Section
      title="Music library"
      aside={
        !adding && (
          <button className="btn h-8 px-3" onClick={() => setAdding(true)}>
            Add a track
          </button>
        )
      }
    >
      <p className="mb-5 max-w-3xl text-sm leading-relaxed text-soft">
        Background music for Shorts. Add only tracks you are allowed to use (royalty-free libraries, Creative Commons,
        or music you bought). Popular songs get a video claimed or muted. The library starts empty, and Shorts are made
        without music until a licensed track matches the moment.
      </p>
      <AudioLibraryImport />
      {!meta.data || tracks.isPending ? (
        <Spinner />
      ) : tracks.isError ? (
        <ErrorNote>{tracks.error.message}</ErrorNote>
      ) : (
        <div className="space-y-4">
          {adding && <AddTrack meta={meta.data} onDone={() => setAdding(false)} />}
          {tracks.data.length === 0 && !adding ? (
            <Empty title="No tracks yet">Use “Add a track” to add your first licensed track.</Empty>
          ) : (
            <ul className="space-y-3">
              {tracks.data.map((t) => (
                <TrackCard key={t.id} track={t} meta={meta.data} />
              ))}
            </ul>
          )}
        </div>
      )}
    </Section>
  )
}
