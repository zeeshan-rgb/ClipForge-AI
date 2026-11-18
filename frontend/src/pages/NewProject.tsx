import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type ProjectOptions, type SourceKind } from '../api'
import { OptionsForm, optionsError } from '../components/OptionsForm'
import { RightsFields, type RightsValue } from '../components/RightsFields'
import { downloadBlocker, SOURCE_LABEL } from '../format'
import { Badge, ErrorNote, PageHeader, Section, Spinner } from '../ui'

function platformOf(url: string): Exclude<SourceKind, 'local'> | null {
  let host: string
  try {
    const u = new URL(url.trim())
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
    host = u.hostname.toLowerCase()
  } catch {
    return null
  }
  const is = (h: string) => host === h || host.endsWith(`.${h}`)
  if (is('youtube.com') || is('youtu.be')) return 'youtube'
  if (is('twitch.tv')) return 'twitch'
  if (is('kick.com')) return 'kick'
  return null
}

export default function NewProject() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const system = useQuery({ queryKey: ['system'], queryFn: api.system, staleTime: 60_000 })
  const defaults = useQuery({ queryKey: ['defaults'], queryFn: api.defaults })

  const [source, setSource] = useState<'url' | 'local'>('url')
  const [url, setUrl] = useState('')
  const [path, setPath] = useState('')
  const [title, setTitle] = useState('')
  const [rights, setRights] = useState<RightsValue>({ status: 'unverified', creator: '', note: '' })
  const [options, setOptions] = useState<ProjectOptions | null>(null)
  const [permissionId, setPermissionId] = useState<number | null>(null)
  const approved = useQuery({ queryKey: ['approved-streamers'], queryFn: api.approvedStreamers })
  const choosePermission = (id: string) => {
    const st = approved.data?.find((a) => String(a.id) === id)
    setPermissionId(st ? st.id : null)
    setRights(st ? { status: 'creator_permission', creator: st.name, note: st.note } : { status: 'unverified', creator: '', note: '' })
  }

  useEffect(() => {
    if (defaults.data && options === null) setOptions(defaults.data)
  }, [defaults.data, options])

  const pick = useMutation({
    mutationFn: api.pickFile,
    onSuccess: (r) => {
      if (r.path) setPath(r.path)
    },
  })

  const create = useMutation({
    mutationFn: api.createProject,
    onSuccess: (project) => {
      void qc.invalidateQueries({ queryKey: ['projects'] })
      void qc.invalidateQueries({ queryKey: ['stats'] })
      navigate(`/analysis/${project.id}`)
    },
  })

  if (!options || !system.data) {
    if (defaults.isError) return <ErrorNote>{defaults.error.message}</ErrorNote>
    if (system.isError) return <ErrorNote>{system.error.message}</ErrorNote>
    return <Spinner />
  }

  const platform = platformOf(url)
  const sourceError =
    source === 'url'
      ? url.trim() === ''
        ? 'Paste a video link.'
        : platform === null
          ? 'That is not a YouTube, Twitch or Kick link. For other videos, use a local file.'
          : null
      : path.trim() === ''
        ? 'Choose a video file.'
        : null
  const problem = sourceError ?? optionsError(options)
  const blocker = source === 'url' ? downloadBlocker(rights.status, rights.creator, rights.note) : null

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (problem) return
    create.mutate({
      source,
      url: source === 'url' ? url.trim() : undefined,
      path: source === 'local' ? path.trim() : undefined,
      title: title.trim() || undefined,
      creator: rights.status === 'own' ? undefined : rights.creator.trim() || undefined,
      rights_status: rights.status,
      rights_note: rights.status === 'own' ? undefined : rights.note.trim() || undefined,
      options,
      permission_id: permissionId ?? undefined,
    })
  }

  return (
    <form onSubmit={submit} className="max-w-3xl">
      <PageHeader title="New project" subtitle="One long video per project. ClipForge finds the moments worth turning into Shorts." />

      <div className="space-y-6">
        <Section title="Video">
          <div className="mb-5 inline-flex rounded-lg border border-line bg-bg p-1" role="tablist" aria-label="Video source">
            {(['url', 'local'] as const).map((s) => (
              <button
                key={s}
                type="button"
                role="tab"
                aria-selected={source === s}
                onClick={() => setSource(s)}
                className={`cursor-pointer rounded-md px-4 py-1.5 text-sm transition-colors ${
                  source === s ? 'bg-raised font-medium text-ink' : 'text-soft hover:text-ink'
                }`}
              >
                {s === 'url' ? 'Video link' : 'File on this computer'}
              </button>
            ))}
          </div>

          {source === 'url' ? (
            <div>
              <label className="label" htmlFor="url">
                YouTube, Twitch or Kick link
              </label>
              <div className="flex items-center gap-3">
                <input
                  id="url"
                  className="input"
                  placeholder="https://www.youtube.com/watch?v=…"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  autoFocus
                />
                {platform && <Badge tone="good">{SOURCE_LABEL[platform]}</Badge>}
              </div>
              <p className="hint">
                Public videos and finished stream recordings only. ClipForge does not sign in, so private,
                members-only and age-restricted videos need to be added as a file instead.
              </p>
            </div>
          ) : (
            <div>
              <label className="label" htmlFor="path">
                Video file
              </label>
              <div className="flex gap-2">
                <input
                  id="path"
                  className="input"
                  placeholder="D:\Videos\stream.mp4"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                />
                <button type="button" className="btn h-10 shrink-0" onClick={() => pick.mutate()} disabled={pick.isPending}>
                  {pick.isPending ? 'Choosing…' : 'Browse…'}
                </button>
              </div>
              {pick.isError && <p className="mt-1.5 text-xs text-bad">{pick.error.message}</p>}
              <p className="hint">
                The file is read where it is, not copied. Supported: {system.data.video_extensions.join(' ')}
              </p>
            </div>
          )}

          <div className="mt-5">
            <label className="label" htmlFor="title">
              Project name <span className="font-normal text-faint">(optional)</span>
            </label>
            <input
              id="title"
              className="input"
              placeholder={source === 'local' ? 'Defaults to the file name' : "Defaults to the video's title"}
              value={title}
              maxLength={300}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
        </Section>

        <Section title="Whose footage is this?">
          {approved.data && approved.data.length > 0 && (
            <div className="mb-5">
              <label className="label" htmlFor="permission">Permission recorded on the Permissions page</label>
              <select id="permission" className="input" value={permissionId ?? ''} onChange={(e) => choosePermission(e.target.value)}>
                <option value="">None: fill in the rights below</option>
                {approved.data.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.platform}){a.conditions ? ` · ${a.conditions}` : ''}
                  </option>
                ))}
              </select>
              <p className="hint">
                ClipForge checks that the video really is from this streamer before downloading it. A link with their
                channel name in it is matched automatically.
              </p>
            </div>
          )}
          <RightsFields value={rights} onChange={(r) => { setRights(r); setPermissionId(null) }} />
          {source === 'url' && blocker && (
            <div className="mt-4 rounded-lg border border-accent/30 bg-accent/10 px-3.5 py-2.5 text-sm text-accent">
              This link will be saved but not downloaded. {blocker} You can also add the video as a file later.
            </div>
          )}
        </Section>

        <Section title="What to make">
          <OptionsForm value={options} categories={system.data.categories} onChange={setOptions} />
        </Section>

        {create.isError && <ErrorNote>{create.error.message}</ErrorNote>}

        <div className="flex items-center gap-4 pb-8">
          <button type="submit" className="btn btn-primary h-10 px-5" disabled={problem !== null || create.isPending}>
            {create.isPending
              ? 'Creating…'
              : source === 'local'
                ? 'Create project and prepare video'
                : blocker
                  ? 'Save project without downloading'
                  : 'Create project and download'}
          </button>
          {problem && <span className="text-sm text-soft">{problem}</span>}
        </div>
      </div>
    </form>
  )
}
