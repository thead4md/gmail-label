import { useEffect, useState } from 'react'
import { X, Sparkles, Send, Trash2, Check } from 'lucide-react'
import { toast } from 'sonner'
import { useCompose } from '../../hooks/useCompose'
import { useAccount } from '../../hooks/useAccount'
import {
  useAiDraft,
  useApproveDraft,
  useCreateDraft,
  useDiscardDraft,
  useDraft,
  useReplyDefaults,
  useSendDraft,
  draftErrorMessage,
} from '../../hooks/useDrafts'
import { Button } from '../ui/Button'

/** Compose panel — a deliberate THREE-STEP gate: Save Draft, Approve, and
 * Send are three separate user actions (three separate requests), never
 * collapsible into fewer. The real enforcement that a draft cannot be sent
 * without a separate prior approval lives server-side
 * (feedback.handle_approve_and_send re-reads the draft's status fresh from
 * the database) — this component's job is only to present the three steps
 * as genuinely separate actions, matching the guarantee exactly. */
export function ComposeSheet() {
  const { target, closeCompose } = useCompose()
  const [account] = useAccount()
  const [draftId, setDraftId] = useState<number | null>(null)
  const [to, setTo] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')

  const replyDefaults = useReplyDefaults(target?.mode === 'reply' ? target.gmailId : undefined)
  const createDraft = useCreateDraft()
  const aiDraft = useAiDraft()
  const draftQuery = useDraft(draftId)
  const approve = useApproveDraft(draftId)
  const discard = useDiscardDraft(draftId)
  const send = useSendDraft(draftId, account)

  useEffect(() => {
    if (!target) {
      setDraftId(null)
      setTo('')
      setSubject('')
      setBody('')
      return
    }
    if (target.mode === 'reply' && replyDefaults.data) {
      setTo(replyDefaults.data.to_addrs)
      setSubject(replyDefaults.data.subject)
    } else if (target.mode === 'new') {
      // Honor a prefilled recipient/subject (e.g. a "Nudge" from a waiting-on
      // loop); falls back to blank for a plain Compose.
      setTo(target.toAddrs ?? '')
      setSubject(target.subject ?? '')
    }
    setBody('')
    setDraftId(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, replyDefaults.data])

  if (!target) return null

  const draft = draftQuery.data

  async function handleSaveDraft() {
    try {
      const res = await createDraft.mutateAsync({
        account,
        in_reply_to_gmail_id: target?.mode === 'reply' ? target.gmailId : undefined,
        thread_id: target?.mode === 'reply' ? target.threadId : undefined,
        to_addrs: to,
        subject,
        body_text: body,
      })
      setDraftId(res.id)
    } catch (e) {
      toast.error(draftErrorMessage(e))
    }
  }

  async function handleAiDraft() {
    if (target?.mode !== 'reply' || !target.gmailId) return
    try {
      const res = await aiDraft.mutateAsync(target.gmailId)
      setBody(res.body_text)
    } catch (e) {
      toast.error(draftErrorMessage(e))
    }
  }

  async function handleApprove() {
    try {
      await approve.mutateAsync()
      toast.success('Draft approved — you can now send it.')
    } catch (e) {
      toast.error(draftErrorMessage(e))
    }
  }

  async function handleDiscard() {
    try {
      await discard.mutateAsync()
      toast('Draft discarded.')
      closeCompose()
    } catch (e) {
      toast.error(draftErrorMessage(e))
    }
  }

  async function handleSend() {
    try {
      await send.mutateAsync()
      toast.success('Sent.')
    } catch (e) {
      toast.error(draftErrorMessage(e))
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-end bg-black/30 sm:items-center sm:justify-center">
      <div
        className="absolute inset-0"
        onClick={() => (draft?.status === 'sent' || !draftId ? closeCompose() : undefined)}
      />
      <div className="relative z-10 flex h-[85vh] w-full max-w-xl flex-col overflow-hidden rounded-t-2xl border border-border-strong bg-surface shadow-[var(--shadow-lg)] sm:h-auto sm:max-h-[80vh] sm:rounded-2xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="text-sm font-semibold">
            {target.mode === 'reply' ? 'Reply' : 'New message'}
            {draft && (
              <span className="ml-2 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-semibold uppercase text-text-muted">
                {draft.status.replace('_', ' ')}
              </span>
            )}
          </div>
          <button onClick={closeCompose} className="rounded-md p-1 text-text-faint hover:bg-surface-2 hover:text-text">
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {!draftId ? (
            <div className="flex flex-col gap-3">
              <Field label="To">
                <input
                  value={to}
                  onChange={(e) => setTo(e.target.value)}
                  className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-[13px] outline-none focus:border-accent"
                  placeholder="recipient@example.com"
                />
              </Field>
              <Field label="Subject">
                <input
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  className="w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-[13px] outline-none focus:border-accent"
                />
              </Field>
              <Field label="Message">
                <textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={10}
                  className="w-full resize-none rounded-lg border border-border bg-surface-2 px-3 py-2 text-[13px] outline-none focus:border-accent"
                />
              </Field>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <Field label="To">
                <div className="text-[13px]">{draft?.to_addrs}</div>
              </Field>
              <Field label="Subject">
                <div className="text-[13px]">{draft?.subject}</div>
              </Field>
              <Field label="Message">
                <div className="whitespace-pre-wrap rounded-lg border border-border bg-surface-2 px-3 py-2 text-[13px] text-text-muted">
                  {draft?.body_text}
                </div>
              </Field>
              {draft?.status === 'sent' && (
                <div className="flex items-center gap-2 rounded-lg bg-success-soft px-3 py-2 text-[13px] text-success">
                  <Check size={15} /> Sent{draft.gmail_message_id ? '' : ' (dry-run)'}.
                </div>
              )}
              {draft?.status === 'send_failed' && (
                <div className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
                  The last send attempt failed.
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
          {!draftId && (
            <>
              {target.mode === 'reply' && (
                <Button variant="ghost" onClick={handleAiDraft} disabled={aiDraft.isPending}>
                  <Sparkles size={14} /> {aiDraft.isPending ? 'Drafting…' : 'Draft with AI'}
                </Button>
              )}
              <div className="flex-1" />
              <Button variant="primary" onClick={handleSaveDraft} disabled={createDraft.isPending || !to || !subject}>
                Save Draft
              </Button>
            </>
          )}
          {draftId && draft?.status === 'pending_review' && (
            <>
              <Button variant="danger" onClick={handleDiscard}>
                <Trash2 size={14} /> Discard
              </Button>
              <Button variant="success" onClick={handleApprove} disabled={approve.isPending}>
                <Check size={14} /> Approve
              </Button>
            </>
          )}
          {draftId && draft?.status === 'approved' && (
            <>
              <Button variant="danger" onClick={handleDiscard}>
                <Trash2 size={14} /> Discard
              </Button>
              <Button variant="primary" onClick={handleSend} disabled={send.isPending}>
                <Send size={14} /> {send.isPending ? 'Sending…' : 'Send'}
              </Button>
            </>
          )}
          {draftId && draft?.status === 'send_failed' && (
            <Button variant="default" onClick={handleApprove}>
              Re-approve for retry
            </Button>
          )}
          {draftId && draft?.status === 'sent' && (
            <Button variant="default" onClick={closeCompose} className="ml-auto">
              Close
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-bold uppercase tracking-wider text-text-faint">{label}</span>
      {children}
    </label>
  )
}
