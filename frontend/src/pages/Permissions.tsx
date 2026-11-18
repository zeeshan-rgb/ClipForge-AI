import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useEffect, useState } from 'react'
import { api, type OutreachSettings, type OutreachStatus, type Streamer, type StreamerStatus } from '../api'
import { ago } from '../format'
import type { Tone } from '../format'
import { Badge, Empty, ErrorNote, PageHeader, Section, Spinner } from '../ui'

const STATUS: Record<StreamerStatus, { label: string; tone: Tone }> = {
  looking_up: { label: 'Looking up', tone: 'info' },
  no_contact: { label: 'Needs an email', tone: 'warn' },
  queued: { label: 'Queued', tone: 'info' },
  sent: { label: 'Asked', tone: 'info' },
  followed_up: { label: 'Followed up', tone: 'info' },
  replied: { label: 'Replied: your decision', tone: 'warn' },
  approved: { label: 'Permission given', tone: 'good' },
  declined: { label: 'Declined', tone: 'bad' },
  no_reply: { label: 'No reply', tone: 'neutral' },
  bounced: { label: 'Email bounced', tone: 'bad' },
  failed: { label: 'Problem', tone: 'bad' },
  paused: { label: 'Paused', tone: 'neutral' },
}

const PLATFORM: Record<string, string> = { youtube: 'YouTube', twitch: 'Twitch', kick: 'Kick' }

function followers(n: number | null) {
  if (n == null) return null
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M followers`
  if (n >= 1_000) return `${Math.round(n / 1_000)}K followers`
  return `${n} followers`
}

export default function Permissions() {
  const status = useQuery({ queryKey: ['outreach'], queryFn: api.outreach, refetchInterval: 30_000 })
  const streamers = useQuery({ queryKey: ['streamers'], queryFn: api.streamers, refetchInterval: 30_000 })

  return (
    <div>
      <PageHeader
        title="Permissions"
        subtitle="Paste a streamer's channel link. ClipForge finds their public contact address, writes a personal request and sends it from your Gmail, a few a day. Replies are noticed automatically; you decide what counts as permission."
      />
      <div className="space-y-6">
        {status.isPending ? <Spinner /> : status.isError ? <ErrorNote>{status.error.message}</ErrorNote> : (
          <>
            <GmailCard s={status.data} />
            <SendingCard s={status.data} />
          </>
        )}
        <AddStreamer />
        <Section title="Requests" aside={streamers.data && <Badge>{streamers.data.length}</Badge>}>
          {streamers.isPending ? <Spinner /> : streamers.isError ? <ErrorNote>{streamers.error.message}</ErrorNote> :
            streamers.data.length === 0 ? (
              <Empty title="No streamers yet">Add a streamer's channel link above to ask for permission.</Empty>
            ) : (
              <div className="space-y-3">{streamers.data.map((st) => <StreamerCard key={st.id} st={st} />)}</div>
            )}
        </Section>
        {status.data && <TemplateCard s={status.data} />}
      </div>
    </div>
  )
}

function GmailCard({ s }: { s: OutreachStatus }) {
  const qc = useQueryClient()
  const connect = useMutation({ mutationFn: api.gmailConnect, onSuccess: ({ url }) => window.open(url, '_blank', 'noopener') })
  const disconnect = useMutation({ mutationFn: api.gmailDisconnect, onSuccess: (d) => qc.setQueryData(['outreach'], d) })
  return (
    <Section
      title="Gmail"
      aside={s.connected ? <Badge tone="good">Connected · {s.address}</Badge> : <Badge>Not connected</Badge>}
    >
      {!s.connected && (
        <ol className="mb-4 list-decimal space-y-1.5 pl-5 text-sm leading-relaxed text-soft">
          <li>In the same Google Cloud project as your YouTube connection, enable the <span className="text-ink">Gmail API</span>.</li>
          <li>Press Connect Gmail and sign in on Google's page with the address the requests should come from.</li>
          <li>Allow both: sending email, and reading it (only to notice replies to these requests).</li>
        </ol>
      )}
      <div className="flex flex-wrap gap-2">
        {s.connected ? (
          <button className="btn" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>Disconnect</button>
        ) : (
          <button className="btn btn-primary" disabled={!s.client || connect.isPending} onClick={() => connect.mutate()}>
            Connect Gmail
          </button>
        )}
        <button className="btn" onClick={() => void qc.invalidateQueries({ queryKey: ['outreach'] })}>Check again</button>
      </div>
      {!s.client && <p className="mt-2 text-xs text-soft">First add the Google Cloud client file in Settings → YouTube channel.</p>}
      {connect.isSuccess && !s.connected && (
        <p className="mt-2 text-xs text-soft">Google's sign-in opened in a new tab. When it says “Gmail connected”, press Check again.</p>
      )}
      {s.last_error && <div className="mt-3"><ErrorNote>{s.last_error}</ErrorNote></div>}
      {(connect.error ?? disconnect.error) && <div className="mt-3"><ErrorNote>{(connect.error ?? disconnect.error)!.message}</ErrorNote></div>}
    </Section>
  )
}

function SendingCard({ s }: { s: OutreachStatus }) {
  const qc = useQueryClient()
  const [form, setForm] = useState<OutreachSettings>(s.settings)
  useEffect(() => setForm(s.settings), [s.settings])
  const save = useMutation({ mutationFn: api.outreachSettings, onSuccess: (d) => qc.setQueryData(['outreach'], d) })
  const set = <K extends keyof OutreachSettings>(k: K, v: OutreachSettings[K]) => setForm({ ...form, [k]: v })
  const num = (k: 'daily_limit' | 'spacing_minutes' | 'followup_days' | 'close_days', label: string, hint: string) => (
    <div>
      <label className="label" htmlFor={`o-${k}`}>{label}</label>
      <input id={`o-${k}`} type="number" className="input" value={form[k]} onChange={(e) => set(k, Number(e.target.value))} />
      <p className="hint">{hint}</p>
    </div>
  )
  return (
    <Section
      title="Sending"
      aside={<Badge tone={s.missing ? 'warn' : 'neutral'}>{s.sent_last_day} of {s.settings.daily_limit} sent in the last 24 h</Badge>}
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <label className="label" htmlFor="o-sender">Your name</label>
          <input id="o-sender" className="input" placeholder="Shown as the sender and signature" value={form.sender_name}
            onChange={(e) => set('sender_name', e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="o-channel">Your channel name</label>
          <input id="o-channel" className="input" placeholder={s.channel_name || 'Channel name'} value={form.channel_name}
            onChange={(e) => set('channel_name', e.target.value)} />
          <p className="hint">Empty = your connected YouTube channel's name.</p>
        </div>
        <div>
          <label className="label" htmlFor="o-url">Your channel link</label>
          <input id="o-url" className="input" placeholder={s.channel_url || 'https://www.youtube.com/@…'} value={form.channel_url}
            onChange={(e) => set('channel_url', e.target.value)} />
        </div>
      </div>
      <div className="mt-4 grid gap-4 sm:grid-cols-4">
        {num('daily_limit', 'Emails a day', 'Requests and follow-ups together (1–20).')}
        {num('spacing_minutes', 'Minutes between emails', 'Spaced out so it reads as a person writing.')}
        {num('followup_days', 'Follow up after (days)', 'One polite follow-up, in the same thread.')}
        {num('close_days', 'Close after (days)', 'After the follow-up with no reply: “No reply”.')}
      </div>
      <label className="mt-4 flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-0.5 accent-accent" checked={form.auto_send} onChange={(e) => set('auto_send', e.target.checked)} />
        <span>
          Send automatically
          <span className="hint block">Off: requests wait in the queue until you press Send now.</span>
        </span>
      </label>
      {s.missing && <p className="mt-3 text-sm text-accent">{s.missing}</p>}
      <div className="mt-4 flex gap-2">
        <button className="btn btn-primary" disabled={save.isPending} onClick={() => save.mutate(form)}>Save</button>
      </div>
      {save.error && <div className="mt-3"><ErrorNote>{save.error.message}</ErrorNote></div>}
    </Section>
  )
}

function AddStreamer() {
  const qc = useQueryClient()
  const [url, setUrl] = useState('')
  const [email, setEmail] = useState('')
  const add = useMutation({
    mutationFn: () => api.addStreamer(url.trim(), email.trim()),
    onSuccess: () => {
      setUrl('')
      setEmail('')
      void qc.invalidateQueries({ queryKey: ['streamers'] })
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (url.trim()) add.mutate()
  }
  return (
    <Section title="Ask a streamer">
      <form onSubmit={submit} className="flex flex-wrap gap-2">
        <input aria-label="Streamer link" className="input min-w-0 flex-[2]" placeholder="https://www.youtube.com/@name, twitch.tv/name or kick.com/name"
          value={url} onChange={(e) => setUrl(e.target.value)} />
        <input aria-label="Email (optional)" className="input min-w-0 flex-1" placeholder="Email, if you already know it (optional)"
          value={email} onChange={(e) => setEmail(e.target.value)} />
        <button className="btn btn-primary" disabled={!url.trim() || add.isPending}>Add</button>
      </form>
      <p className="hint mt-2">
        A YouTube channel link finds the most. YouTube hides many business emails behind “View email address” on the
        channel's About page (it needs a CAPTCHA, which ClipForge does not get past): open it and paste the address here.
      </p>
      {add.error && <div className="mt-3"><ErrorNote>{add.error.message}</ErrorNote></div>}
      <BulkAdd />
    </Section>
  )
}

function BulkAdd() {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const bulk = useMutation({
    mutationFn: () => api.addStreamers(text),
    onSuccess: (r) => {
      if (r.skipped.length === 0) setText('')
      void qc.invalidateQueries({ queryKey: ['streamers'] })
    },
  })
  const count = text.split('\n').filter((l) => l.trim() && !l.trim().startsWith('#')).length
  return (
    <details className="mt-4 border-t border-line pt-4">
      <summary className="cursor-pointer text-sm font-medium">Paste a whole list</summary>
      <p className="hint mt-2">
        One streamer per line: a channel link, optionally followed by a comma and their email. Lines starting with # are
        ignored. They are looked up a few at a time, and the emails go out within your daily limit, oldest first.
      </p>
      <textarea aria-label="Streamer list" className="input mt-2 min-h-48 font-mono text-xs"
        placeholder={'https://www.twitch.tv/name\nhttps://www.youtube.com/@name, business@example.com\nkick.com/name'}
        value={text} onChange={(e) => setText(e.target.value)} />
      <div className="mt-2 flex items-center gap-3">
        <button className="btn btn-primary" disabled={count === 0 || bulk.isPending} onClick={() => bulk.mutate()}>
          Add {count || ''} streamer{count === 1 ? '' : 's'}
        </button>
        {bulk.data && (
          <span className="text-sm text-soft">
            Added {bulk.data.added.length}{bulk.data.skipped.length ? `, skipped ${bulk.data.skipped.length}` : ''}.
          </span>
        )}
      </div>
      {bulk.data && bulk.data.skipped.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-soft">
          {bulk.data.skipped.map((x, i) => <li key={i}><span className="text-ink">{x.line}</span>: {x.reason}</li>)}
        </ul>
      )}
      {bulk.error && <div className="mt-3"><ErrorNote>{bulk.error.message}</ErrorNote></div>}
    </details>
  )
}

function StreamerCard({ st }: { st: Streamer }) {
  const qc = useQueryClient()
  const [email, setEmail] = useState(st.contact_email ?? '')
  const [editing, setEditing] = useState(false)
  const [subject, setSubject] = useState(st.subject ?? '')
  const [body, setBody] = useState(st.body ?? '')
  const [evidence, setEvidence] = useState('')
  const [conditions, setConditions] = useState('')
  useEffect(() => {
    setEmail(st.contact_email ?? '')
    setSubject(st.subject ?? '')
    setBody(st.body ?? '')
  }, [st.contact_email, st.subject, st.body])
  const done = () => {
    void qc.invalidateQueries({ queryKey: ['streamers'] })
    void qc.invalidateQueries({ queryKey: ['outreach'] })
  }
  const act = useMutation({ mutationFn: (fn: () => Promise<unknown>) => fn(), onSuccess: done })
  const tone = STATUS[st.status]
  const canEditText = ['queued', 'no_contact', 'paused', 'failed', 'bounced'].includes(st.status)
  const canSetEmail = ['no_contact', 'queued', 'bounced', 'paused', 'failed'].includes(st.status)

  return (
    <div className="rounded-lg border border-line bg-bg p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <a className="font-medium hover:underline" href={st.channel_url ?? st.url} target="_blank" rel="noreferrer">
              {st.name || st.handle || st.url}
            </a>
            <Badge>{PLATFORM[st.platform] ?? st.platform}</Badge>
            {followers(st.followers) && <span className="text-xs text-faint">{followers(st.followers)}</span>}
          </div>
          {st.status_detail && <p className="mt-1 text-sm text-soft">{st.status_detail}</p>}
          {st.sent_at && <p className="mt-1 text-xs text-faint">Asked {ago(st.sent_at)}{st.followup_at ? ` · followed up ${ago(st.followup_at)}` : ''}</p>}
        </div>
        <Badge tone={tone.tone}>{tone.label}</Badge>
      </div>

      {st.emails.length > 0 && (
        <p className="mt-2 text-xs text-faint">
          Found: {st.emails.map((e) => `${e.email} (${e.found})`).join(' · ')}
        </p>
      )}

      {canSetEmail && (
        <div className="mt-3 flex flex-wrap gap-2">
          <input aria-label="Contact email" className="input min-w-0 flex-1" placeholder="Contact email" value={email}
            onChange={(e) => setEmail(e.target.value)} />
          <button className="btn" disabled={act.isPending || email.trim() === (st.contact_email ?? '')}
            onClick={() => act.mutate(() => api.editStreamer(st.id, { contact_email: email.trim() }))}>
            Use this email
          </button>
        </div>
      )}

      {st.reply && (
        <div className="mt-3 rounded-lg border border-line bg-panel p-3">
          <div className="text-xs text-faint">Reply from {st.reply.from} · {st.reply.date}</div>
          <p className="mt-1 whitespace-pre-wrap text-sm">{st.reply.text || st.reply.snippet}</p>
        </div>
      )}

      {st.status === 'approved' && (st.evidence || st.conditions) && (
        <p className="mt-2 text-sm text-soft">
          {st.evidence && <>Evidence: {st.evidence}. </>}
          {st.conditions && <>Conditions: <span className="text-ink">{st.conditions}</span></>}
        </p>
      )}

      {['replied', 'no_contact', 'no_reply', 'sent', 'followed_up', 'queued', 'paused'].includes(st.status) && (
        <details className="mt-3" open={st.status === 'replied'}>
          <summary className="cursor-pointer text-sm text-soft">Record the decision</summary>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <input aria-label="Conditions" className="input" placeholder="Their conditions, e.g. “credit on screen, no monetising”"
              value={conditions} onChange={(e) => setConditions(e.target.value)} />
            <input aria-label="Evidence" className="input"
              placeholder={st.reply ? 'Extra evidence (optional: the reply is kept)' : 'Where they said yes (required without a reply)'}
              value={evidence} onChange={(e) => setEvidence(e.target.value)} />
          </div>
          <div className="mt-2 flex gap-2">
            <button className="btn btn-primary" disabled={act.isPending}
              onClick={() => act.mutate(() => api.approveStreamer(st.id, evidence.trim(), conditions.trim()))}>
              They said yes
            </button>
            <button className="btn" disabled={act.isPending} onClick={() => act.mutate(() => api.declineStreamer(st.id))}>
              They said no
            </button>
          </div>
        </details>
      )}

      {editing && canEditText && (
        <div className="mt-3 space-y-2">
          <input aria-label="Subject" className="input" value={subject} onChange={(e) => setSubject(e.target.value)} />
          <textarea aria-label="Message" className="input min-h-64 font-mono text-xs" value={body} onChange={(e) => setBody(e.target.value)} />
          <button className="btn" disabled={act.isPending}
            onClick={() => act.mutate(async () => { await api.editStreamer(st.id, { subject, body }); setEditing(false) })}>
            Save text
          </button>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {st.status === 'queued' && (
          <button className="btn btn-primary" disabled={act.isPending} onClick={() => act.mutate(() => api.sendStreamer(st.id))}>Send now</button>
        )}
        {canEditText && st.body && (
          <button className="btn" onClick={() => setEditing(!editing)}>{editing ? 'Close text' : 'View / edit text'}</button>
        )}
        {(st.status === 'sent' || st.status === 'followed_up') && (
          <button className="btn" disabled={act.isPending} onClick={() => act.mutate(() => api.checkStreamer(st.id))}>Check for a reply</button>
        )}
        {['failed', 'no_contact'].includes(st.status) && (
          <button className="btn" disabled={act.isPending} onClick={() => act.mutate(() => api.lookupStreamer(st.id))}>Look up again</button>
        )}
        {!['approved', 'declined'].includes(st.status) && (
          <button className="btn" disabled={act.isPending} onClick={() => act.mutate(() => api.deleteStreamer(st.id))}>Remove</button>
        )}
      </div>
      {act.error && <div className="mt-3"><ErrorNote>{act.error.message}</ErrorNote></div>}
    </div>
  )
}

function TemplateCard({ s }: { s: OutreachStatus }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ subject: s.settings.subject, body: s.settings.body, followup_body: s.settings.followup_body })
  useEffect(() => setForm({ subject: s.settings.subject, body: s.settings.body, followup_body: s.settings.followup_body }),
    [s.settings.subject, s.settings.body, s.settings.followup_body])
  const save = useMutation({ mutationFn: () => api.outreachSettings(form), onSuccess: (d) => qc.setQueryData(['outreach'], d) })
  const preview = useMutation({ mutationFn: () => api.outreachPreview(form) })
  return (
    <Section title="Email text">
      <details>
        <summary className="cursor-pointer text-sm text-soft">
          Edit the request and the follow-up. Fields: {'{streamer}'} {'{channel_name}'} {'{channel_url}'} {'{sender_name}'} {'{platform}'}
        </summary>
        <div className="mt-3 space-y-3">
          <input aria-label="Subject template" className="input" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
          <textarea aria-label="Request template" className="input min-h-72 font-mono text-xs" value={form.body}
            onChange={(e) => setForm({ ...form, body: e.target.value })} />
          <textarea aria-label="Follow-up template" className="input min-h-40 font-mono text-xs" value={form.followup_body}
            onChange={(e) => setForm({ ...form, followup_body: e.target.value })} />
          <div className="flex gap-2">
            <button className="btn btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save text</button>
            <button className="btn" disabled={preview.isPending} onClick={() => preview.mutate()}>Preview</button>
          </div>
          {preview.data && (
            <div className="rounded-lg border border-line bg-bg p-3 text-sm">
              <div className="font-medium">{preview.data.subject}</div>
              <pre className="mt-2 whitespace-pre-wrap font-sans text-soft">{preview.data.body}</pre>
            </div>
          )}
          {(save.error ?? preview.error) && <ErrorNote>{(save.error ?? preview.error)!.message}</ErrorNote>}
        </div>
      </details>
    </Section>
  )
}
