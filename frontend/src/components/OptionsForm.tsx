import type { CaptionPreset, ContextLevel, DurationMode, Layout, MusicMode, ProjectOptions } from '../api'
import { CATEGORY_LABEL } from '../format'
import { EMPTY_OVERRIDES } from './CaptionStyleForm'

export const DURATION_PRESETS = [15, 20, 30, 35, 45, 60, 90]
const MIN_TARGET = 10
const MAX_TARGET = 180
const FLEX_FACTOR = 1.6

export const MODE_INFO: Record<DurationMode, { label: string; help: string }> = {
  strict: {
    label: 'Strict',
    help: 'Never longer than the target. Setup may be trimmed to fit; if nothing complete fits, no clip is made.',
  },
  smart: {
    label: 'Smart',
    help: 'The target is an aim. The clip may run a little over or under so the last sentence can finish.',
  },
  flexible: {
    label: 'Flexible',
    help: 'The target is a preference. The clip runs as long as the whole moment needs, and says why.',
  },
}

const CONTEXT_INFO: Record<ContextLevel, string> = {
  tight: 'Tight: start at the moment itself',
  balanced: 'Balanced: allow one line of lead-in',
  full: 'Full: allow up to three lines of lead-in',
}

/** The range a single clip may land in, for the chosen mode. */
export function durationRange(o: ProjectOptions): { low: number; high: number } {
  const t = o.target_duration
  if (o.duration_mode === 'strict') return { low: 0, high: t }
  if (o.duration_mode === 'smart')
    return { low: t * (1 - o.tolerance_pct / 100), high: t * (1 + o.tolerance_pct / 100) }
  return { low: 0, high: t * FLEX_FACTOR }
}

export function describeDuration(o: ProjectOptions): string {
  const { low, high } = durationRange(o)
  const t = o.target_duration
  if (o.duration_mode === 'strict') return `${t} s or less`
  if (o.duration_mode === 'smart') return `about ${t} s (${Math.round(low)}–${Math.round(high)} s)`
  return `about ${t} s, up to ${Math.round(high)} s if the moment needs it`
}

export function optionsError(o: ProjectOptions): string | null {
  if (o.categories.length === 0) return 'Pick at least one category.'
  if (!(o.max_clips >= 1 && o.max_clips <= 50)) return 'Maximum clips must be between 1 and 50.'
  if (!(o.target_duration >= MIN_TARGET && o.target_duration <= MAX_TARGET))
    return `Target length must be between ${MIN_TARGET} and ${MAX_TARGET} seconds.`
  if (!Number.isInteger(o.target_duration)) return 'Target length must be a whole number of seconds.'
  if (!(o.tolerance_pct >= 0 && o.tolerance_pct <= 30)) return 'Tolerance must be between 0 and 30 percent.'
  if (!(o.max_parts >= 2 && o.max_parts <= 5)) return 'Maximum parts must be between 2 and 5.'
  if (!(o.music_gap_db >= 6 && o.music_gap_db <= 30)) return 'Music volume must be between 6 and 30 dB under the speech.'
  return null
}

function NumberField({
  id,
  label,
  value,
  min,
  max,
  suffix,
  onChange,
}: {
  id: string
  label: string
  value: number
  min: number
  max: number
  suffix?: string
  onChange: (n: number) => void
}) {
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type="number"
          className="input tabular-nums"
          min={min}
          max={max}
          value={Number.isNaN(value) ? '' : value}
          onChange={(e) => onChange(e.target.valueAsNumber)}
        />
        {suffix && (
          <span className="pointer-events-none absolute inset-y-0 right-9 flex items-center text-xs text-faint">
            {suffix}
          </span>
        )}
      </div>
    </div>
  )
}

function SmartDuration({
  value,
  set,
}: {
  value: ProjectOptions
  set: <K extends keyof ProjectOptions>(key: K, v: ProjectOptions[K]) => void
}) {
  const custom = !DURATION_PRESETS.includes(value.target_duration)
  const range = durationRange(value)
  const partsUpTo = Math.round(range.high * value.max_parts)

  return (
    <fieldset className="rounded-lg border border-line p-4">
      <legend className="px-1.5 text-sm font-medium">Smart Duration</legend>

      <div className="mb-1.5 text-sm text-soft">Target length</div>
      <div className="flex flex-wrap items-center gap-2">
        {DURATION_PRESETS.map((seconds) => {
          const on = value.target_duration === seconds
          return (
            <button
              key={seconds}
              type="button"
              aria-pressed={on}
              onClick={() => set('target_duration', seconds)}
              className={`cursor-pointer rounded-lg border px-3 py-1.5 text-sm tabular-nums transition-colors ${
                on ? 'border-accent bg-accent/15 text-accent' : 'border-line bg-raised text-soft hover:text-ink'
              }`}
            >
              {seconds === 60 ? '60 s (1 min)' : `${seconds} s`}
            </button>
          )
        })}
        <div className="flex items-center gap-2">
          <label htmlFor="custom-target" className="text-sm text-soft">
            Custom
          </label>
          <input
            id="custom-target"
            type="number"
            min={MIN_TARGET}
            max={MAX_TARGET}
            className={`input h-9 w-20 tabular-nums ${custom ? 'border-accent' : ''}`}
            value={Number.isNaN(value.target_duration) ? '' : value.target_duration}
            onChange={(e) => set('target_duration', e.target.valueAsNumber)}
          />
          <span className="text-sm text-soft">s</span>
        </div>
      </div>

      <div className="mt-5 grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Duration mode">
        {(Object.keys(MODE_INFO) as DurationMode[]).map((mode) => (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={value.duration_mode === mode}
            onClick={() => set('duration_mode', mode)}
            className={`cursor-pointer rounded-lg border px-3 py-2.5 text-left transition-colors ${
              value.duration_mode === mode ? 'border-accent bg-accent/10' : 'border-line bg-bg hover:border-faint'
            }`}
          >
            <div className="text-sm font-medium">
              {MODE_INFO[mode].label}
              {mode === 'smart' && <span className="ml-1.5 text-xs font-normal text-faint">recommended</span>}
            </div>
            <div className="mt-0.5 text-xs leading-relaxed text-soft">{MODE_INFO[mode].help}</div>
          </button>
        ))}
      </div>

      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        {value.duration_mode === 'smart' && (
          <NumberField
            id="tolerance"
            label="Tolerance"
            suffix="%"
            min={0}
            max={30}
            value={value.tolerance_pct}
            onChange={(n) => set('tolerance_pct', n)}
          />
        )}
        <div>
          <label className="label" htmlFor="context">
            Context before the moment
          </label>
          <select
            id="context"
            className="input"
            value={value.context}
            onChange={(e) => set('context', e.target.value as ContextLevel)}
          >
            {(Object.keys(CONTEXT_INFO) as ContextLevel[]).map((level) => (
              <option key={level} value={level}>
                {CONTEXT_INFO[level]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="hook">
            Opening hook
          </label>
          <select
            id="hook"
            className="input"
            value={value.hook ?? 'auto'}
            onChange={(e) => set('hook', e.target.value as ProjectOptions['hook'])}
          >
            <option value="auto">Automatic: a 1–3 s flash-forward of the payoff when it comes late</option>
            <option value="cold_open">Always open with the payoff's flash-forward</option>
            <option value="story">Story order only</option>
          </select>
          <p className="hint">The story then plays from its start, so nothing loses its setup. Parts keep story order.</p>
        </div>
      </div>

      <div className="mt-5 space-y-3">
        <label className="flex cursor-pointer items-start gap-2.5 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 accent-accent"
            checked={value.split_parts}
            onChange={(e) => set('split_parts', e.target.checked)}
          />
          <span>
            Split long moments into Part 1, Part 2…
            <span className="block text-xs text-soft">
              A moment too long for one clip is told in connected parts, split only where a sentence has finished.
            </span>
          </span>
        </label>
        {value.split_parts && (
          <div className="ml-6 w-40">
            <NumberField
              id="max-parts"
              label="Maximum parts"
              min={2}
              max={5}
              value={value.max_parts}
              onChange={(n) => set('max_parts', n)}
            />
          </div>
        )}
        <label className="flex cursor-pointer items-start gap-2.5 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 accent-accent"
            checked={value.trim_silence}
            onChange={(e) => set('trim_silence', e.target.checked)}
          />
          <span>
            Remove dead air
            <span className="block text-xs text-soft">
              Silences longer than two seconds inside a clip are shortened. Speech is never touched.
            </span>
          </span>
        </label>
      </div>

      <p className="mt-4 rounded-md bg-bg px-3 py-2 text-xs leading-relaxed text-soft">
        Each clip will be <span className="text-ink">{describeDuration(value)}</span>
        {value.split_parts && (
          <>
            ; moments up to about <span className="text-ink">{partsUpTo} s</span> are split into at most{' '}
            {value.max_parts} parts
          </>
        )}
        . A clip always starts at the beginning of a sentence and ends after a finished one. The target guides the
        cut; it never forces one.
      </p>
    </fieldset>
  )
}

export function OptionsForm({
  value,
  categories,
  onChange,
}: {
  value: ProjectOptions
  categories: string[]
  onChange: (next: ProjectOptions) => void
}) {
  const set = <K extends keyof ProjectOptions>(key: K, v: ProjectOptions[K]) => onChange({ ...value, [key]: v })
  const toggle = (c: string) =>
    set(
      'categories',
      value.categories.includes(c) ? value.categories.filter((x) => x !== c) : [...value.categories, c],
    )
  const all = value.categories.length === categories.length

  return (
    <div className="space-y-6">
      <fieldset>
        <div className="mb-2 flex items-center justify-between">
          <legend className="text-sm font-medium">Moments to look for</legend>
          <button
            type="button"
            className="text-xs text-soft hover:text-ink"
            onClick={() => set('categories', all ? [] : categories)}
          >
            {all ? 'Clear all' : 'Select all'}
          </button>
        </div>
        <div className="flex flex-wrap gap-2">
          {categories.map((c) => {
            const on = value.categories.includes(c)
            return (
              <button
                key={c}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(c)}
                className={`cursor-pointer rounded-full border px-3 py-1.5 text-sm transition-colors ${
                  on
                    ? 'border-accent bg-accent/15 text-accent'
                    : 'border-line bg-raised text-soft hover:border-faint hover:text-ink'
                }`}
              >
                {CATEGORY_LABEL[c] ?? c}
              </button>
            )
          })}
        </div>
      </fieldset>

      <SmartDuration value={value} set={set} />

      <div className="grid gap-5 sm:grid-cols-3">
        <NumberField id="max-clips" label="Maximum clips" min={1} max={50} value={value.max_clips} onChange={(n) => set('max_clips', n)} />
        <div className="sm:col-span-2">
          <label className="label" htmlFor="caption-preset">
            Caption style
          </label>
          <select
            id="caption-preset"
            className="input"
            value={value.caption_style.enabled ? value.caption_style.preset : 'off'}
            onChange={(e) =>
              set(
                'caption_style',
                e.target.value === 'off'
                  ? { ...value.caption_style, enabled: false }
                  : { ...value.caption_style, ...EMPTY_OVERRIDES, enabled: true, preset: e.target.value as CaptionPreset },
              )
            }
          >
            <option value="viral">Viral Highlight (the reference look)</option>
            <option value="bold">Bold Emphasis</option>
            <option value="minimal">Minimal Professional</option>
            <option value="gaming">Gaming</option>
            <option value="off">No captions</option>
          </select>
          <p className="hint">Fine-tune colours, size and position later in the Clip editor.</p>
        </div>
      </div>

      <fieldset className="rounded-lg border border-line p-4">
        <legend className="px-1.5 text-sm font-medium">Background music</legend>
        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="music-mode">
              Music
            </label>
            <select
              id="music-mode"
              className="input"
              value={value.music_mode}
              onChange={(e) => set('music_mode', e.target.value as MusicMode)}
            >
              <option value="auto">Automatic: music that suits each moment</option>
              <option value="always">Always: even for non-stop talking</option>
              <option value="off">No music</option>
            </select>
            <p className="hint">
              Only tracks with complete licence details are ever used, and only if one matches the moment. Clips that
              are almost non-stop talking get none in Automatic.
            </p>
          </div>
          <div>
            <div className="mb-1.5 flex items-baseline justify-between">
              <label className="text-sm font-medium" htmlFor="music-gap">
                Music volume
              </label>
              <span className="text-sm tabular-nums text-accent">{value.music_gap_db} dB under speech</span>
            </div>
            <input
              id="music-gap"
              type="range"
              min={6}
              max={30}
              value={value.music_gap_db}
              onChange={(e) => set('music_gap_db', e.target.valueAsNumber)}
              className="w-full accent-accent"
            />
            <p className="hint">
              Lower numbers are louder. The music is lowered a further 10 dB while someone is speaking.
            </p>
          </div>
        </div>
      </fieldset>

      <fieldset className="rounded-lg border border-line p-4">
        <legend className="px-1.5 text-sm font-medium">Vertical framing</legend>
        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="layout">
              Layout
            </label>
            <select
              id="layout"
              className="input"
              value={value.layout}
              onChange={(e) => set('layout', e.target.value as Layout)}
            >
              <option value="auto">Automatic: split screen with a facecam, otherwise follow the speaker</option>
              <option value="full">Full screen, following the speaker's face</option>
              <option value="split">Split screen: streamer on top, gameplay below</option>
              <option value="facecam_inset">Gameplay with the facecam in a corner</option>
              <option value="reaction">Facecam reaction: the streamer fills the frame</option>
            </select>
            <p className="hint">
              Layouts that need a facecam fall back to full screen when none is found. Each clip can be changed in the
              Clip editor.
            </p>
          </div>
          <div className="space-y-3 pt-1">
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 accent-accent"
                checked={value.zoom}
                onChange={(e) => set('zoom', e.target.checked)}
              />
              <span>
                Push in on the key moment
                <span className="hint block">A slow 15% zoom, held for a moment and eased back.</span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 accent-accent"
                checked={value.part_labels}
                onChange={(e) => set('part_labels', e.target.checked)}
              />
              <span>
                Show “PART 1/2” on connected parts
                <span className="hint block">For the first 2.5 seconds, placed clear of faces and captions.</span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 accent-accent"
                checked={value.credit_tag}
                onChange={(e) => set('credit_tag', e.target.checked)}
              />
              <span>
                Credit the creator on screen
                <span className="hint block">
                  A small “@name” in a corner for clips from someone else’s video. The description always credits them.
                </span>
              </span>
            </label>
          </div>
        </div>
      </fieldset>

      <div>
        <div className="mb-1.5 flex items-baseline justify-between">
          <label className="text-sm font-medium" htmlFor="min-score">
            Minimum viral score
          </label>
          <span className="text-sm tabular-nums text-accent">{value.min_score}</span>
        </div>
        <input
          id="min-score"
          type="range"
          min={0}
          max={100}
          step={5}
          value={value.min_score}
          onChange={(e) => set('min_score', e.target.valueAsNumber)}
          className="w-full accent-accent"
        />
        <p className="hint">Moments scoring below this are found and listed, but not selected for clips.</p>
      </div>
    </div>
  )
}
