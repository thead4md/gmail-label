import type { QueueItem } from '../../lib/types'
import { LabelChip } from '../ui/LabelChip'
import { ChannelChip } from '../ui/ChannelChip'
import { ConfidenceBar } from '../ui/ConfidenceBar'
import { TrustBadge } from '../ui/TrustBadge'

export function ReasonPanel({ item }: { item: QueueItem }) {
  const reason = item.reason_json || {}
  const mlConf = reason.ml_confidence ?? item.ml_confidence
  const llmConf = reason.llm_confidence ?? item.llm_confidence
  const tier = item.trust_tier || reason.trust_tier

  const rows: Array<[string, React.ReactNode]> = []
  if (item.primary_label) rows.push(['Label', <LabelChip label={item.primary_label} />])
  if (item.confidence !== null) rows.push(['Confidence', <ConfidenceBar value={item.confidence} />])
  if (tier) rows.push(['Sender trust', <TrustBadge tier={tier} />])
  if (item.channel) rows.push(['Channel', <ChannelChip channel={item.channel} />])
  if (reason.rule_matches?.length)
    rows.push([
      'Rules matched',
      <span className="flex flex-wrap gap-1">
        {reason.rule_matches.map((r, i) => (
          <code key={i} className="rounded bg-surface-3 px-1.5 py-0.5 text-[11px]">
            {r}
          </code>
        ))}
      </span>,
    ])
  if (mlConf !== null && mlConf !== undefined) rows.push(['ML confidence', <ConfidenceBar value={mlConf} />])
  if (llmConf !== null && llmConf !== undefined) rows.push(['LLM confidence', <ConfidenceBar value={llmConf} />])
  if (reason.thread_summary) rows.push(['Thread', <em className="text-text-muted">{reason.thread_summary.slice(0, 150)}</em>])

  if (rows.length === 0) return null

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface-2 p-3 text-[12px]">
      {rows.map(([k, v], i) => (
        <div key={i} className="flex items-baseline gap-2 border-b border-border-strong/40 pb-1.5 last:border-0 last:pb-0">
          <span className="min-w-[110px] shrink-0 text-[10px] font-bold uppercase tracking-wider text-text-faint">{k}</span>
          <span className="text-text">{v}</span>
        </div>
      ))}
    </div>
  )
}
