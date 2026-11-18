export type StageStatus = 'pending' | 'running' | 'done' | 'failed'
export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface Stage {
  name: string
  label: string
  status: StageStatus
  progress: number
  message: string | null
  attempts: number
}

export interface Job {
  id: number
  kind: string
  status: JobStatus
  project_id: number | null
  progress: number
  error: string | null
  created_at: string
  updated_at: string
  stages: Stage[]
}

export interface Media {
  duration: number | null
  width: number | null
  height: number | null
  fps: number | null
  video_codec: string | null
  audio_codec: string | null
  has_audio: boolean
  format: string | null
  size_bytes: number | null
}

export type RightsStatus = 'own' | 'licensed' | 'creator_permission' | 'unverified'
export type SourceKind = 'local' | 'youtube' | 'twitch' | 'kick'
export type ProjectStatus = 'created' | 'processing' | 'ready' | 'needs_rights' | 'failed'

export interface SourceMeta {
  platform: string
  video_id: string | null
  title: string | null
  creator: string | null
  creator_url: string | null
  duration: number | null
  upload_date: string | null
  licence: string | null
  webpage_url: string | null
}

export interface AnalysisSummary {
  audio: { events: number; silences: number; silence_seconds: number } | null
  video: { cuts: number } | null
  transcript: {
    chunks_done: number
    lines: number
    language: string | null
    model: string | null
    done: boolean
  } | null
  moments: {
    found: number
    selected: number
    provider?: string
    visually_checked?: number
    visual_failed?: number
    note?: string
  } | null
  clips: {
    made: number
    not_valid: number
    duplicates_dropped: number
    multi_part_moments: number
    target_seconds: number
    mode: DurationMode
    split: boolean
  } | null
  captions: {
    made: number
    passed: number
    flagged_words: number
    preset: CaptionPreset
    enabled: boolean
  } | null
  music: { mode: MusicMode; with_music: number; without_music: number } | null
  framing: {
    layout: Layout
    layouts: Partial<Record<Layout, number>>
    detector: boolean
    facecam: { note: string } | null
    facecam_clips: number
    face_clips: number
  } | null
  final: { made: number; passed: number; encoder: string; folder: string } | null
}

export interface Moment {
  id: number
  rank: number
  start: number
  end: number
  peak: number
  category: string
  score: number
  subscores: Record<string, number>
  /** every score the moment has, AI and measured (context blends the AI with the transcript check) */
  parts: Record<string, number>
  measured: {
    context_code?: number
    context_notes?: string[]
    audio_quality?: number
    audio_detail?: { notes: string[] }
    visual_quality?: number
    visual_detail?: { notes: string[] }
  }
  flags: { no_dialogue?: boolean; reaction?: boolean; gameplay?: boolean }
  reason: string
  excerpt: string
  visual: string | null
  visual_checked: boolean
  source: 'ai_text' | 'signals'
  selected: boolean
}

export interface Weights {
  weights: Record<string, number>
  defaults: Record<string, number>
  meaning: Record<string, string>
  source: Record<string, 'ai' | 'measured' | 'ai+measured'>
  note: string
}

export interface Signals {
  duration: number
  has_audio: boolean
  has_video: boolean
  loudness: number[]
  loudness_hop: number
  motion: number[]
  motion_hop: number
  silences: [number, number][]
  events: { t: number; kind: 'surge' | 'surge_after_silence'; rise_db: number }[]
  cuts: number[]
}

export interface TranscriptLine {
  id: number
  start: number
  end: number
  text: string
}

export interface Transcript {
  language: string | null
  model: string | null
  done: boolean
  segments: TranscriptLine[]
}

export interface RightsRecord {
  rights_status: RightsStatus
  creator?: string
  rights_note?: string
}

export interface ProjectOptions {
  categories: string[]
  max_clips: number
  min_score: number
  target_duration: number
  duration_mode: DurationMode
  tolerance_pct: number
  split_parts: boolean
  max_parts: number
  context: ContextLevel
  trim_silence: boolean
  hook: 'auto' | 'story' | 'cold_open'
  caption_style: CaptionStyle
  music_mode: MusicMode
  music_gap_db: number
  layout: Layout
  zoom: boolean
  part_labels: boolean
  credit_tag: boolean
}

export type MusicMode = 'auto' | 'off' | 'always'
export type Layout = 'auto' | 'full' | 'facecam_inset' | 'reaction' | 'split'

export const LAYOUT_LABEL: Record<Layout, string> = {
  auto: 'Automatic',
  full: 'Full screen',
  facecam_inset: 'Gameplay with facecam',
  reaction: 'Facecam reaction',
  split: 'Split screen',
}

export interface ClipFraming {
  requested: Layout | null
  layout: Exclude<Layout, 'auto'>
  layout_label: string
  reason: string
  subject: { kind: 'facecam' | 'face' | 'none'; share: number; note: string; facecam: number[] | null }
  samples: number
  detector: boolean
  follow: boolean
  zoom_at: number | null
}

export interface ClipFinal {
  name: string
  exists: boolean
  size_bytes: number
  width: number
  height: number
  fps: number
  duration: number
  encoder: string
  layout: string
  loudness: { lufs: number | null; true_peak: number | null; gain_db?: number | null; target_lufs?: number }
  checks: { name: string; ok: boolean; detail: string; status?: QcStatus }[]
  passed: boolean
  score?: number
  qc?: QualityReport
  rendered_at: string
}

export type QcStatus = 'ok' | 'warn' | 'fail'

export interface QualityReport {
  score: number
  passed: boolean
  fails: number
  warnings: number
  checks: { name: string; label: string; status: QcStatus; detail: string }[]
  music_gap?: { gap_db: number; speech_db: number; music_in_speech_db: number }
}

export interface ClipMeta {
  title: string
  description: string
  hashtags: string[]
  pinned_comment: string
  by: string
  notes?: string[]
  edited: boolean
  written_at?: string
}

export interface ClipExport {
  folder: string
  video: string
  at: string
  anyway: boolean
}

export interface ExportResult {
  exported: { clip_id: number; rank: number; part: number; folder: string; video: string; files: string[] }[]
  refused: { clip_id: number; rank: number; part: number; reason: string }[]
  folder: string
}

export interface YouTubeStatus {
  client: boolean
  client_error: string | null
  cloud_project: string | null
  connected: boolean
  channel: { id: string; title: string; url: string } | null
  connected_at: string | null
  scopes: string[]
  defaults: { category_id: string; made_for_kids: boolean }
  categories: Record<string, string>
}

export interface ClipYouTube {
  video_id?: string
  url?: string
  studio_url?: string
  privacy?: 'private' | 'unlisted' | 'public'
  upload_status?: string
  channel?: string
  title?: string
  uploaded_at?: string
  published_at?: string | null
  state?: 'uploading' | 'uploaded' | 'removed' | 'scheduled' | null
  note?: string | null
  publish_at?: string | null
  planned_publish_at?: string | null
  schedule_note?: string | null
  scheduled_by?: 'auto' | 'owner' | null
  rejection?: string | null
  failure?: string | null
}

export interface StoryLine {
  id: string
  role: 'hook' | 'context' | 'reaction' | 'outro'
  text: string
  audio: string | null
  seconds: number | null
  at: number | null
  auto_at?: boolean
}

export interface Story {
  lines: StoryLine[]
  cards: { id: string; text: string; at: number; seconds: number }[]
  stickers: { id: string; sticker: string; at: number; seconds: number }[]
  drafted_at: string | null
  updated_at?: string
  contribution: { seconds: number; share: number; lines: number; cards: number; stickers: number }
  in_final: { seconds: number; lines: number } | null
  render_needed: boolean
  stickers_available: Record<string, { text: string; moods: string[] }>
}

export interface ScheduleStatus {
  settings: { enabled: boolean; auto: boolean; times: string[]; timezone: string; require_story: boolean }
  upcoming: {
    clip_id: number
    project_id: number
    project: string
    title: string | null
    publish_at: string
    past: boolean
    video_id: string | null
    url: string | null
    state: string | null
    note: string | null
  }[]
  next_slots: string[]
  connected: boolean
  horizon_hours: number
  max_per_day: number
}

export interface PublishChecklist {
  items: { name: string; label: string; status: QcStatus; detail: string; overridable: boolean }[]
  passed: boolean
  passed_with_override: boolean
  fails: number
  warnings: number
  text_differs: boolean
}

export interface ShortRow extends Clip {
  project: { id: number; title: string; rights_status: RightsStatus; creator: string | null }
}

export interface Track {
  id: number
  title: string
  original_name: string | null
  duration: number
  bpm: number | null
  steady_beat: boolean
  loudness_db: number
  moods: string[]
  source: string | null
  licence_name: string | null
  licence_url: string | null
  licence_note: string | null
  attribution: string | null
  requires_attribution: boolean
  usable: boolean
  problems: string[]
  warnings: string[]
  added_at: string
  used_by: number
}

export interface TrackFields {
  title?: string
  moods?: string[]
  source?: string
  licence_name?: string
  licence_url?: string
  licence_note?: string
  attribution?: string
}

export interface MusicMeta {
  moods: { name: string; label: string }[]
  licences: { name: string; requires_attribution: boolean }[]
  category_moods: Record<string, string[]>
  audio_extensions: string[]
  min_gap_db: number
}

export interface ClipMusic {
  used: boolean
  mode: 'auto' | 'manual'
  reason: string
  track_id?: number
  title?: string
  mood?: string | null
  offset?: number
  loop?: boolean
  gain_db?: number
  open_db?: number
  gap_db?: number
  speech_db?: number | null
  level_basis?: string
  beat?: { anchor: number; beat_time: number; kind: 'downbeat' | 'beat'; error_ms: number } | null
  beat_note?: string | null
  licence_name?: string | null
  source?: string | null
  credit?: string | null
  measured?: {
    speech_db: number | null
    music_in_speech_db: number | null
    music_open_db: number | null
    gap_db: number | null
    required_gap_db: number
    ok: boolean
  }
}

export type CaptionPreset = 'viral' | 'bold' | 'minimal' | 'gaming'

export interface CaptionStyle {
  preset: CaptionPreset
  font: string | null
  size: number | null
  text_color: string | null
  highlight_color: string | null
  outline: number | null
  shadow: number | null
  position: number | null
  uppercase: boolean | null
  words_per_line: number | null
  speed: number | null
  enabled: boolean
}

export interface PresetValues {
  name: CaptionPreset
  label: string
  font: string
  size: number
  text_color: string
  highlight_color: string
  emphasis_color: string | null
  outline: number
  shadow: number
  position: number
  uppercase: boolean
  words_per_line: number
  animation: 'pop' | 'none'
  speed: number
}

export interface CaptionPresets {
  presets: PresetValues[]
  fonts: { name: string; file: string }[]
}

export interface CaptionLine {
  start: number
  end: number
  text: string
  words: { s: number; e: number; w: string; p: number }[]
}

export interface ClipCaptions {
  passed: boolean
  word_count: number
  line_count: number
  checks: { name: string; ok: boolean; detail: string }[]
  flagged_words: { t: number; word: string; confidence: number }[]
  lines: CaptionLine[]
  style: CaptionStyle
  position?: number
  moved?: string | null
  face_share?: number
  label?: { text: string; where: string } | null
  tag?: { text: string; where: string } | null
}

export type DurationMode = 'strict' | 'smart' | 'flexible'
export type ContextLevel = 'tight' | 'balanced' | 'full'

export interface ClipReport {
  requested_seconds: number
  actual_seconds: number
  mode: DurationMode
  first_word?: string | null
  last_word?: string | null
  sentence_complete?: boolean | null
  question_answered?: boolean
  event_complete?: boolean | null
  speakers_overlap_at_end?: boolean
  ending_quality?: number | null
  last_word_fully_inside?: boolean
  dead_air_removed?: number
  continues_from_previous?: boolean
  repeats_dialogue?: boolean
  skips_dialogue?: boolean
  reasons: string[]
  problems: string[]
  no_fit?: string
}

export interface Clip {
  id: number
  moment_id: number | null
  rank: number | null
  part: number
  parts: number
  start: number
  end: number
  duration: number
  category: string | null
  score: number | null
  valid: boolean
  report: ClipReport
  has_preview: boolean
  has_vertical: boolean
  captions: ClipCaptions | null
  music: ClipMusic | null
  framing: ClipFraming | null
  final: ClipFinal | null
  meta: ClipMeta | null
  exported: ClipExport | null
  youtube: ClipYouTube | null
  export_blocker: string | null
  status: 'cut' | 'rendered' | 'exported'
  review: 'pending' | 'approved' | 'rejected'
  overridden: boolean
}

export interface Project {
  id: number
  title: string
  source_kind: SourceKind
  source_url: string | null
  source_path: string | null
  creator: string | null
  rights_status: RightsStatus
  rights_note: string | null
  options: ProjectOptions
  media: Media | null
  source_meta: SourceMeta | null
  analysis: AnalysisSummary
  has_thumbnail: boolean
  has_proxy: boolean
  has_audio_file: boolean
  download_blocker: string | null
  status: ProjectStatus
  status_detail: string | null
  clip_count: number
  created_at: string
  updated_at: string
}

export interface Stats {
  projects_total: number
  videos_processed: number
  clips_total: number
  clips_exported: number
  jobs_by_status: Partial<Record<JobStatus, number>>
  active_jobs: Job[]
  recent_projects: Project[]
}

export interface SystemInfo {
  version: string
  python: string
  os: string
  ffmpeg: string | null
  ffprobe: string | null
  data_dir: string
  disk_free_gb: number
  disk_total_gb: number
  gemini_route: string
  fonts_installed: number
  fonts_total: number
  ai_models: string
  speech_model: string
  categories: string[]
  video_extensions: string[]
}

export interface NewProject {
  source: 'local' | 'url'
  path?: string
  url?: string
  title?: string
  creator?: string
  rights_status: RightsStatus
  rights_note?: string
  options: ProjectOptions
  permission_id?: number
}

export type StreamerStatus =
  | 'looking_up' | 'no_contact' | 'queued' | 'sent' | 'followed_up' | 'replied'
  | 'approved' | 'declined' | 'no_reply' | 'bounced' | 'failed' | 'paused'

export interface Streamer {
  id: number
  url: string
  platform: 'youtube' | 'twitch' | 'kick'
  handle: string | null
  name: string | null
  followers: number | null
  channel_url: string | null
  links: { url: string; found: string }[]
  emails: { email: string; found: string }[]
  contact_email: string | null
  status: StreamerStatus
  status_detail: string | null
  subject: string | null
  body: string | null
  sent_at: string | null
  followup_at: string | null
  reply: { from: string; date: string; snippet: string; text: string } | null
  evidence: string | null
  conditions: string | null
  decided_at: string | null
  created_at: string
}

export interface OutreachSettings {
  auto_send: boolean
  daily_limit: number
  spacing_minutes: number
  followup_days: number
  close_days: number
  sender_name: string
  channel_name: string
  channel_url: string
  subject: string
  body: string
  followup_body: string
}

export interface OutreachStatus {
  client: boolean
  connected: boolean
  address: string | null
  settings: OutreachSettings
  channel_name: string
  channel_url: string
  missing: string | null
  sent_last_day: number
  last_error: string | null
  last_tick: string | null
  counts: Partial<Record<StreamerStatus, number>>
}

export interface ApprovedStreamer {
  id: number
  name: string
  platform: string
  channel_url: string | null
  conditions: string | null
  note: string
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    })
  } catch {
    throw new ApiError('Cannot reach ClipForge. Is the app still running?', 0)
  }
  if (res.status === 204) return undefined as T
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const message =
      body && typeof body === 'object' && 'error' in body && typeof body.error === 'string'
        ? body.error
        : `Request failed (${res.status})`
    throw new ApiError(message, res.status)
  }
  return body as T
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  stats: () => request<Stats>('/api/stats'),
  system: () => request<SystemInfo>('/api/system/info'),
  pickFile: () => post<{ path: string | null }>('/api/system/pick-file'),
  defaults: () => request<ProjectOptions>('/api/settings/defaults'),
  saveDefaults: (o: ProjectOptions) =>
    request<ProjectOptions>('/api/settings/defaults', { method: 'PUT', body: JSON.stringify(o) }),
  projects: () => request<Project[]>('/api/projects'),
  project: (id: number) => request<Project>(`/api/projects/${id}`),
  createProject: (p: NewProject) => post<Project>('/api/projects', p),
  deleteProject: (id: number) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  updateRights: (id: number, r: RightsRecord) =>
    request<Project>(`/api/projects/${id}/rights`, { method: 'PATCH', body: JSON.stringify(r) }),
  attachFile: (id: number, path: string) => post<Project>(`/api/projects/${id}/attach`, { path }),
  signals: (id: number) => request<Signals>(`/api/projects/${id}/signals`),
  transcript: (id: number) => request<Transcript>(`/api/projects/${id}/transcript`),
  analyze: (id: number) => post<Project>(`/api/projects/${id}/analyze`),
  moments: (id: number) => request<Moment[]>(`/api/projects/${id}/moments`),
  detect: (id: number) => post<Project>(`/api/projects/${id}/detect`),
  updateOptions: (id: number, o: ProjectOptions) =>
    request<Project>(`/api/projects/${id}/options`, { method: 'PATCH', body: JSON.stringify(o) }),
  clips: (id: number) => request<Clip[]>(`/api/projects/${id}/clips`),
  cut: (id: number) => post<Project>(`/api/projects/${id}/cut`),
  reviewClip: (projectId: number, clipId: number, review: Clip['review'], override = false) =>
    request<Clip>(`/api/projects/${projectId}/clips/${clipId}`, {
      method: 'PATCH',
      body: JSON.stringify({ review, override }),
    }),
  tracks: () => request<Track[]>('/api/music'),
  musicMeta: () => request<MusicMeta>('/api/music/meta'),
  pickAudio: () => post<{ path: string | null }>('/api/system/pick-audio'),
  importTrack: (t: TrackFields & { path: string }) => post<Track>('/api/music/import', t),
  updateTrack: (id: number, t: TrackFields) =>
    request<Track>(`/api/music/${id}`, { method: 'PATCH', body: JSON.stringify(t) }),
  deleteTrack: (id: number, force = false) =>
    request<void>(`/api/music/${id}${force ? '?force=true' : ''}`, { method: 'DELETE' }),
  setClipMusic: (projectId: number, clipId: number, mode: 'auto' | 'none' | 'track', trackId?: number) =>
    request<Clip>(`/api/projects/${projectId}/clips/${clipId}/music`, {
      method: 'PATCH',
      body: JSON.stringify({ mode, track_id: trackId ?? null }),
    }),
  captionPresets: () => request<CaptionPresets>('/api/captions/presets'),
  setClipLayout: (projectId: number, clipId: number, layout: Layout) =>
    request<Clip>(`/api/projects/${projectId}/clips/${clipId}/framing`, {
      method: 'PATCH',
      body: JSON.stringify({ layout }),
    }),
  renderFinal: (projectId: number, clipIds?: number[]) =>
    request<Project>(`/api/projects/${projectId}/render-final`, {
      method: 'POST',
      body: JSON.stringify({ clip_ids: clipIds ?? null }),
    }),
  shorts: () => request<ShortRow[]>('/api/shorts'),
  youtube: () => request<YouTubeStatus>('/api/youtube'),
  youtubeClient: (path: string) => post<YouTubeStatus>('/api/youtube/client', { path }),
  youtubeConnect: () => post<{ url: string }>('/api/youtube/connect'),
  youtubeDisconnect: () => post<YouTubeStatus>('/api/youtube/disconnect'),
  youtubeDefaults: (d: YouTubeStatus['defaults']) =>
    request<YouTubeStatus>('/api/youtube/defaults', { method: 'PUT', body: JSON.stringify(d) }),
  uploadShorts: (projectId: number, clipIds: number[], override = false) =>
    post<Project>(`/api/projects/${projectId}/upload`, { clip_ids: clipIds, override }),
  publishShort: (projectId: number, clipId: number, privacy: 'public' | 'unlisted' | 'private', title: string) =>
    post<Clip>(`/api/projects/${projectId}/clips/${clipId}/publish`, { privacy, title, confirm: true }),
  schedule: () => request<ScheduleStatus>('/api/schedule'),
  story: (projectId: number, clipId: number) => request<Story>(`/api/projects/${projectId}/clips/${clipId}/story`),
  draftStory: (projectId: number, clipId: number) => post<Story>(`/api/projects/${projectId}/clips/${clipId}/story/draft`),
  saveStory: (projectId: number, clipId: number, s: unknown) =>
    request<Story>(`/api/projects/${projectId}/clips/${clipId}/story`, { method: 'PUT', body: JSON.stringify(s) }),
  uploadStoryTake: async (projectId: number, clipId: number, lineId: string, blob: Blob): Promise<Story> => {
    const form = new FormData()
    form.append('file', blob, blob.type.includes('mp4') ? 'take.m4a' : 'take.webm')
    const res = await fetch(`/api/projects/${projectId}/clips/${clipId}/story/lines/${lineId}/audio`, { method: 'POST', body: form })
    const body = (await res.json().catch(() => null)) as { error?: string } | null
    if (!res.ok) throw new ApiError(body?.error ?? `Request failed (${res.status})`, res.status)
    return body as unknown as Story
  },
  deleteStoryTake: (projectId: number, clipId: number, lineId: string) =>
    request<Story>(`/api/projects/${projectId}/clips/${clipId}/story/lines/${lineId}/audio`, { method: 'DELETE' }),
  storyTakeUrl: (projectId: number, clipId: number, lineId: string, v: number | null) =>
    `/api/projects/${projectId}/clips/${clipId}/story/lines/${lineId}/audio?v=${v ?? 0}`,
  importMusicFolder: (folder: string) =>
    post<{ added: Track[]; skipped: { file: string; reason: string }[] }>('/api/music/import-folder', { folder }),
  putSchedule: (s: Partial<ScheduleStatus['settings']>) =>
    request<ScheduleStatus>('/api/schedule', { method: 'PUT', body: JSON.stringify(s) }),
  scheduleShort: (projectId: number, clipId: number, at?: string) =>
    post<Clip>(`/api/projects/${projectId}/clips/${clipId}/schedule`, { confirm: true, at: at ?? null }),
  unscheduleShort: (projectId: number, clipId: number) =>
    request<Clip>(`/api/projects/${projectId}/clips/${clipId}/schedule`, { method: 'DELETE' }),
  publishChecklist: (projectId: number, clipId: number) =>
    request<PublishChecklist>(`/api/projects/${projectId}/clips/${clipId}/publish-checklist`),
  sendTextToYouTube: (projectId: number, clipId: number) =>
    post<Clip>(`/api/projects/${projectId}/clips/${clipId}/youtube-text`),
  refreshShort: (projectId: number, clipId: number) =>
    post<Clip>(`/api/projects/${projectId}/clips/${clipId}/youtube-refresh`),
  editClipText: (
    projectId: number,
    clipId: number,
    text: { title: string; description: string; hashtags: string[]; pinned_comment: string },
  ) => request<Clip>(`/api/projects/${projectId}/clips/${clipId}/meta`, { method: 'PATCH', body: JSON.stringify(text) }),
  writeAgain: (projectId: number, clipIds: number[]) =>
    request<Project>(`/api/projects/${projectId}/metadata`, { method: 'POST', body: JSON.stringify({ clip_ids: clipIds }) }),
  exportShorts: (projectId: number, clipIds?: number[], override = false) =>
    request<ExportResult>(`/api/projects/${projectId}/export`, {
      method: 'POST',
      body: JSON.stringify({ clip_ids: clipIds ?? null, override }),
    }),
  openExports: (projectId?: number) =>
    post<{ folder: string }>(`/api/system/open-exports${projectId ? `?project_id=${projectId}` : ''}`),
  updateCaptions: (projectId: number, style: CaptionStyle) =>
    request<Project>(`/api/projects/${projectId}/captions`, { method: 'PATCH', body: JSON.stringify(style) }),
  weights: () => request<Weights>('/api/settings/weights'),
  saveWeights: (w: Record<string, number>) =>
    request<Weights>('/api/settings/weights', { method: 'PUT', body: JSON.stringify(w) }),
  jobs: (projectId?: number) =>
    request<Job[]>(projectId === undefined ? '/api/jobs' : `/api/jobs?project_id=${projectId}`),
  selfTest: (stageSeconds: number) => post<Job>('/api/jobs/selftest', { stage_seconds: stageSeconds }),
  cancelJob: (id: number) => post<Job>(`/api/jobs/${id}/cancel`),
  retryJob: (id: number) => post<Job>(`/api/jobs/${id}/retry`),
  outreach: () => request<OutreachStatus>('/api/outreach'),
  outreachSettings: (s: Partial<OutreachSettings>) =>
    request<OutreachStatus>('/api/outreach/settings', { method: 'PUT', body: JSON.stringify(s) }),
  outreachPreview: (s: Partial<OutreachSettings>) =>
    post<{ subject: string; body: string; followup: string }>('/api/outreach/preview', s),
  gmailConnect: () => post<{ url: string }>('/api/gmail/connect'),
  gmailDisconnect: () => post<OutreachStatus>('/api/gmail/disconnect'),
  streamers: () => request<Streamer[]>('/api/streamers'),
  approvedStreamers: () => request<ApprovedStreamer[]>('/api/streamers/approved'),
  addStreamer: (url: string, email?: string) => post<Streamer>('/api/streamers', { url, email: email || null }),
  addStreamers: (text: string) =>
    post<{ added: Streamer[]; skipped: { line: string; reason: string }[] }>('/api/streamers/bulk', { text }),
  editStreamer: (id: number, s: { contact_email?: string; subject?: string; body?: string }) =>
    request<Streamer>(`/api/streamers/${id}`, { method: 'PATCH', body: JSON.stringify(s) }),
  sendStreamer: (id: number) => post<Streamer>(`/api/streamers/${id}/send`),
  checkStreamer: (id: number) => post<Streamer>(`/api/streamers/${id}/check`),
  lookupStreamer: (id: number) => post<Streamer>(`/api/streamers/${id}/lookup`),
  approveStreamer: (id: number, evidence: string, conditions: string) =>
    post<Streamer>(`/api/streamers/${id}/approve`, { evidence, conditions }),
  declineStreamer: (id: number) => post<Streamer>(`/api/streamers/${id}/decline`, {}),
  deleteStreamer: (id: number) => request<void>(`/api/streamers/${id}`, { method: 'DELETE' }),
}
