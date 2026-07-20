import { useState } from 'react'
import { toast } from 'sonner'
import { ExternalLink, Sparkles } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'
import { TrustBadge } from '../components/ui/TrustBadge'
import { ConfidenceBar } from '../components/ui/ConfidenceBar'
import { Avatar } from '../components/ui/Avatar'
import { useAccount } from '../hooks/useAccount'
import {
  useAutomate,
  useCreateNlRule,
  useDecideLabelSuggestion,
  useSetAutopilot,
  useSetLabelPriority,
} from '../hooks/useAutomate'
import { formatTs } from '../lib/format'
import { ApiError } from '../lib/api'

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

export function AutomatePage() {
  const [account] = useAccount()
  const [days, setDays] = useState(7)
  const { data, isLoading } = useAutomate(account, days)
  const setAutopilot = useSetAutopilot()
  const setPriority = useSetLabelPriority()
  const createRule = useCreateNlRule(account)
  const decideSuggestion = useDecideLabelSuggestion()
  const [ruleText, setRuleText] = useState('')
  const [weights, setWeights] = useState<Record<string, number>>({})

  if (isLoading || !data) return <div className="p-6 text-text-faint">Loading…</div>

  const d = data.digest

  async function onSubmitRule() {
    if (!ruleText.trim()) return
    try {
      const res = await createRule.mutateAsync(ruleText.trim())
      const scope = res.match_pattern ? ` when subject matches /${res.match_pattern}/` : ' (all messages)'
      toast.success(`Rule created: ${res.sender} → ${res.label}${scope}`)
      setRuleText('')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Error creating rule.')
    }
  }

  return (
    <div>
      <PageHeader title="Automate" subtitle="Digest, sender trust, rules, and model health" />
      <div className="mx-auto max-w-3xl px-6 py-5">
        <Section icon="📊" title="Activity digest">
          <div className="mb-3 flex items-center gap-3">
            <input type="range" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-40 accent-[var(--accent)]" />
            <span className="text-[12px] tabular-nums text-text-muted">{days} days</span>
          </div>
          <div className="grid grid-cols-5 gap-3">
            <Stat label="Classified" value={d.classified} />
            <Stat label="Executed" value={d.executed} />
            <Stat label="In queue" value={d.queued} />
            <Stat label="Corrections" value={d.corrections} />
            <Stat label="Exec errors" value={d.execute_failed} tone={d.execute_failed ? 'danger' : undefined} />
          </div>
          {!!d.pending_reply_needed && (
            <div className="mt-3 rounded-lg bg-accent-soft px-3 py-2 text-[12px] text-accent">
              💬 {d.pending_reply_needed} pending item(s) flagged Reply Needed
            </div>
          )}
        </Section>

        <Section icon="👤" title="Sender profiles">
          {!data.sender_profiles.length ? (
            <EmptyState icon="👤" title="No emails in the database yet." />
          ) : (
            <div className="flex flex-col gap-1.5">
              {data.sender_profiles.map((p) => (
                <div key={p.sender_email} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2">
                  <Avatar sender={p.sender_email} size={28} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[12.5px]">{p.sender_email}</span>
                      <TrustBadge tier={p.trust_tier} />
                    </div>
                    <div className="text-[11px] text-text-faint">
                      {p.total_approved + p.total_rejected > 0 ? (
                        <span className="flex items-center gap-1.5">
                          ✅ {p.total_approved} · ❌ {p.total_rejected} <ConfidenceBar value={p.approval_rate} />
                        </span>
                      ) : (
                        `${p.email_count} emails`
                      )}
                    </div>
                  </div>
                  <label className="flex items-center gap-1.5 text-[11px] text-text-muted">
                    <span>Autopilot</span>
                    <input
                      type="checkbox"
                      checked={!!p.auto_action_eligible}
                      onChange={(e) => {
                        setAutopilot.mutate({ email: p.sender_email, enabled: e.target.checked })
                        toast(e.target.checked ? `Autopilot on — ${p.sender_email}` : `Autopilot off — ${p.sender_email}`)
                      }}
                      className="accent-[var(--accent)]"
                    />
                  </label>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section icon="⚖️" title="Label priority weights">
          <p className="mb-3 text-[12px] text-text-faint">
            Set weights (−20 to +30) to boost or suppress specific labels in scoring.
          </p>
          <div className="flex flex-col gap-2">
            {data.all_labels.map((label) => {
              const current = weights[label] ?? data.label_priorities[label] ?? 0
              return (
                <div key={label} className="flex items-center gap-3">
                  <span className="w-32 shrink-0 text-[12px] font-medium">{label}</span>
                  <input
                    type="range"
                    min={-20}
                    max={30}
                    value={current}
                    onChange={(e) => setWeights((w) => ({ ...w, [label]: Number(e.target.value) }))}
                    onMouseUp={() => setPriority.mutate({ label, weight: current })}
                    onTouchEnd={() => setPriority.mutate({ label, weight: current })}
                    className="flex-1 accent-[var(--accent)]"
                  />
                  <span className="w-8 text-right text-[11px] tabular-nums text-text-muted">{current}</span>
                </div>
              )
            })}
          </div>
        </Section>

        <Section icon="✨" title="Create rule from description">
          <div className="flex gap-2">
            <input
              value={ruleText}
              onChange={(e) => setRuleText(e.target.value)}
              placeholder="e.g., label anything from newsletter@example.com as NEWSLETTER"
              className="flex-1 rounded-lg border border-border bg-surface-2 px-3 py-2 text-[13px] outline-none focus:border-accent"
            />
            <Button variant="primary" onClick={onSubmitRule} disabled={createRule.isPending}>
              <Sparkles size={14} /> Create rule
            </Button>
          </div>
        </Section>

        <Section icon="🏷️" title="Suggested labels">
          {!data.label_suggestions.length ? (
            <p className="text-[12px] text-text-faint">
              No pending suggestions. The discovery loop mines your recent mail for recurring themes about every 1–2 months.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {data.label_suggestions.map((s) => (
                <div key={s.id} className="rounded-lg border border-border bg-surface p-3">
                  <div className="mb-1 text-[13px] font-semibold">
                    🏷️ {s.suggested_label} · {s.email_count} emails
                  </div>
                  {s.rationale && <div className="mb-2 text-[12px] text-text-muted">{s.rationale}</div>}
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="success"
                      onClick={async () => {
                        try {
                          await decideSuggestion.mutateAsync({ id: s.id, decision: 'accepted' })
                          toast.success(`Adopted '${s.suggested_label}'`)
                        } catch (e) {
                          toast.error(e instanceof ApiError ? e.message : 'Error adopting suggestion.')
                        }
                      }}
                    >
                      Adopt
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={async () => {
                        try {
                          await decideSuggestion.mutateAsync({ id: s.id, decision: 'dismissed' })
                          toast('Suggestion dismissed.')
                        } catch (e) {
                          toast.error(e instanceof ApiError ? e.message : 'Error dismissing suggestion.')
                        }
                      }}
                    >
                      Dismiss
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section icon="🤖" title="Model health">
          {!data.model_health ? (
            <EmptyState icon="🤖" title="No model trained yet." subtitle="SSH into the machine and run: python -m mailmind.scripts.train_ml_model" />
          ) : (
            <div className="grid grid-cols-3 gap-3">
              <Stat label="Last trained" value={formatTs(data.model_health.created_at)} />
              <Stat label="Accuracy" value={data.model_health.accuracy ? `${(data.model_health.accuracy * 100).toFixed(1)}%` : 'N/A'} />
              <Stat label="Training samples" value={data.model_health.training_samples} />
            </div>
          )}
        </Section>

        <Section icon="📰" title="Newsletters — unsubscribe">
          {!data.newsletters.length ? (
            <p className="text-[12px] text-text-faint">No newsletters with unsubscribe links yet.</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {data.newsletters.map((n) => (
                <div key={n.sender + n.unsubscribe_url} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2 text-[12px]">
                  <span className="min-w-0 flex-1 truncate">{n.sender}</span>
                  <span className="text-text-faint">{n.email_count} emails</span>
                  <a href={n.unsubscribe_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-accent hover:underline">
                    Unsubscribe <ExternalLink size={11} />
                  </a>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section icon="📬" title="Queue statistics">
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
            {Object.entries(data.queue_stats).map(([k, v]) => (
              <Stat key={k} label={k.replace('_', ' ')} value={v} />
            ))}
          </div>
        </Section>
      </div>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'danger' }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2.5">
      <div className="truncate text-[10px] font-bold uppercase tracking-wider text-text-faint">{label}</div>
      <div className={'text-[18px] font-bold tabular-nums ' + (tone === 'danger' && value ? 'text-danger' : '')}>{value}</div>
    </div>
  )
}
