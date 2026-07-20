import { Fragment, useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { PageHeader } from '../components/layout/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { useAccount } from '../hooks/useAccount'
import { useInsights } from '../hooks/useInsights'
import { channelColor, labelColor } from '../lib/taxonomy'

const WEEKDAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function Section({ icon, title, children }: { icon: string; title: string; children: React.ReactNode }) {
  return (
    <div className="mb-7">
      <div className="mb-2.5 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-text-faint">
        <span>{icon}</span>
        <span>{title}</span>
        <span className="h-px flex-1 bg-border" />
      </div>
      {children}
    </div>
  )
}

const chartTooltipStyle = {
  background: 'var(--surface-2)',
  border: '1px solid var(--border-strong)',
  borderRadius: 8,
  fontSize: 12,
}

export function InsightsPage() {
  const [account] = useAccount()
  const [days, setDays] = useState(30)
  // Debounced separately from the slider's own display value: query
  // fires (and charts remount, via the key= below) only once dragging
  // settles, not once per pixel of drag.
  const [queryDays, setQueryDays] = useState(30)
  useEffect(() => {
    const id = setTimeout(() => setQueryDays(days), 200)
    return () => clearTimeout(id)
  }, [days])
  const { data, isLoading } = useInsights(account, queryDays)

  if (isLoading || !data) return <div className="p-6 text-text-faint">Loading…</div>

  const maxWeekdayCount = Math.max(1, ...data.channel_weekday.map((c) => c.count))
  const channels = [...new Set(data.channel_weekday.map((c) => c.channel))]

  return (
    <div>
      <PageHeader title="Insights" subtitle="Analytics on classification and volume" />
      <div className="mx-auto max-w-3xl px-6 py-5">
        <div className="mb-5 flex items-center gap-3">
          <span className="text-[11px] font-bold uppercase tracking-wider text-text-faint">Window</span>
          <input type="range" min={1} max={90} value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-40 accent-[var(--accent)]" />
          <span className="text-[12px] tabular-nums text-text-muted">{days} days</span>
        </div>

        <Section icon="📊" title="Label distribution">
          {data.label_distribution.length === 0 ? (
            <EmptyState icon="📊" title="No data yet" />
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart key={queryDays} data={data.label_distribution}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} axisLine={{ stroke: 'var(--border)' }} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={chartTooltipStyle} cursor={{ fill: 'var(--surface-3)' }} />
                <Bar dataKey="count" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                  {data.label_distribution.map((d, i) => (
                    <Cell key={i} fill={labelColor(d.label)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Section>

        <Section icon="📨" title="Channel volume">
          {data.channel_distribution.length === 0 ? (
            <EmptyState icon="📨" title="No data yet" />
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart key={queryDays} data={data.channel_distribution} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
                <YAxis dataKey="channel" type="category" width={80} tick={{ fontSize: 10, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={chartTooltipStyle} cursor={{ fill: 'var(--surface-3)' }} />
                <Bar dataKey="count" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                  {data.channel_distribution.map((d, i) => (
                    <Cell key={i} fill={channelColor(d.channel)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Section>

        <Section icon="🗓️" title="Channel × weekday">
          {channels.length === 0 ? (
            <EmptyState icon="🗓️" title="No data yet" />
          ) : (
            <div className="overflow-x-auto">
              <div className="grid gap-1" style={{ gridTemplateColumns: `100px repeat(7, 1fr)` }}>
                <div />
                {WEEKDAY_LABELS.map((d) => (
                  <div key={d} className="pb-1 text-center text-[10px] text-text-faint">
                    {d}
                  </div>
                ))}
                {channels.map((ch) => (
                  <Fragment key={ch}>
                    <div className="truncate pr-2 text-[11px] text-text-muted">{ch}</div>
                    {WEEKDAY_LABELS.map((_, wd) => {
                      const count = data.channel_weekday.find((c) => c.channel === ch && c.weekday === wd)?.count ?? 0
                      const opacity = count / maxWeekdayCount
                      return (
                        <div
                          key={wd}
                          title={`${count}`}
                          className="flex aspect-square items-center justify-center rounded-md text-[10px] font-semibold"
                          style={{ background: `${channelColor(ch)}${Math.round(opacity * 200 + 20).toString(16).padStart(2, '0')}` }}
                        >
                          {count > 0 && count}
                        </div>
                      )
                    })}
                  </Fragment>
                ))}
              </div>
            </div>
          )}
        </Section>

        <Section icon="👤" title="Top senders">
          {data.top_senders.length === 0 ? (
            <EmptyState icon="👤" title="No data yet" />
          ) : (
            <div className="flex flex-col gap-1.5">
              {data.top_senders.slice(0, 10).map((s) => (
                <div key={s.sender} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2 text-[12px]">
                  <span className="min-w-0 flex-1 truncate">{s.sender}</span>
                  <span className="text-text-faint">{s.volume} emails</span>
                  <span className="w-10 text-right text-text-muted">{Math.round(s.approval_rate * 100)}%</span>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section icon="🎯" title="Classification quality by tier">
          {data.tier_quality.length === 0 ? (
            <EmptyState icon="🎯" title="No predictions in this window yet" />
          ) : (
            <>
              <div className="overflow-hidden rounded-xl border border-border">
                <table className="w-full text-[12px]">
                  <thead className="bg-surface-2 text-[10px] uppercase tracking-wider text-text-faint">
                    <tr>
                      <th className="px-3 py-2 text-left">Tier</th>
                      <th className="px-3 py-2 text-right">Predictions</th>
                      <th className="px-3 py-2 text-right">Corrected</th>
                      <th className="px-3 py-2 text-right">Correction rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.tier_quality.map((t) => (
                      <tr key={t.source} className="border-t border-border">
                        <td className="px-3 py-2">{t.source}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{t.total}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{t.corrections}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{(t.correction_rate * 100).toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11px] text-text-faint">
                Correction rate = share of each tier's predictions you later changed. Lower is better.
              </p>
              {data.autopilot_precision.precision !== null && (
                <div className="mt-3 inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-[12px]">
                  <span className="text-text-faint">Autopilot precision</span>
                  <span className="font-bold text-text">{Math.round(data.autopilot_precision.precision * 100)}%</span>
                  <span className="text-text-faint">
                    ({data.autopilot_precision.auto_executed} auto-executed, {data.autopilot_precision.later_corrected} later corrected)
                  </span>
                </div>
              )}
            </>
          )}
        </Section>

        <Section icon="💰" title="LLM spend">
          {!data.llm_cost.calls ? (
            <EmptyState icon="💰" title="No LLM calls recorded in this window yet" />
          ) : (
            <>
              <div className="mb-3 grid grid-cols-3 gap-3">
                <Stat label="Cost (window)" value={`$${data.llm_cost.cost_usd.toFixed(4)}`} />
                <Stat label="Calls" value={data.llm_cost.calls.toLocaleString()} />
                <Stat label="Avg latency" value={`${data.llm_cost.avg_latency_ms} ms`} />
              </div>
              {!!data.llm_cost.by_kind.length && (
                <div className="overflow-hidden rounded-xl border border-border">
                  <table className="w-full text-[12px]">
                    <thead className="bg-surface-2 text-[10px] uppercase tracking-wider text-text-faint">
                      <tr>
                        <th className="px-3 py-2 text-left">Model</th>
                        <th className="px-3 py-2 text-left">Kind</th>
                        <th className="px-3 py-2 text-right">Calls</th>
                        <th className="px-3 py-2 text-right">Cost ($)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.llm_cost.by_kind.map((k, i) => (
                        <tr key={i} className="border-t border-border">
                          <td className="px-3 py-2">{k.model}</td>
                          <td className="px-3 py-2">{k.kind}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{k.calls}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{k.cost_usd.toFixed(4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </Section>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2.5">
      <div className="text-[10px] font-bold uppercase tracking-wider text-text-faint">{label}</div>
      <div className="text-[18px] font-bold tabular-nums">{value}</div>
    </div>
  )
}
