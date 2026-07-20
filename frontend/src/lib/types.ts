export interface ReasonJson {
  primary_label?: string
  score?: number
  score_breakdown?: Record<string, number>
  rule_matches?: string[]
  ml_confidence?: number | null
  llm_confidence?: number | null
  trust_tier?: string
  thread_summary?: string | null
  reply_needed?: boolean
  similar_past_actions?: Array<{ action?: string }>
  action_items?: string[]
  deadlines?: string[]
  unsubscribe_url?: string | null
}

export interface QueueItem {
  id: number
  email_gmail_id: string
  prediction_id: number | null
  action: string | null
  status: string
  confidence: number | null
  priority_score: number | null
  reason_json: ReasonJson
  created_at: number
  updated_at: number | null
  reviewed_at: number | null
  executed_at: number | null
  subject: string | null
  sender: string | null
  date_ts: number | null
  snippet: string | null
  display_name: string | null
  trust_tier: string | null
  total_approved: number | null
  total_rejected: number | null
  auto_action_eligible: number | null
  primary_label: string | null
  prediction_confidence: number | null
  ml_confidence: number | null
  llm_confidence: number | null
  channel: string | null
  was_auto?: boolean
}

export interface EmailListItem {
  gmail_id: string
  thread_id: string | null
  sender: string | null
  subject: string | null
  snippet: string | null
  date_ts: number | null
  primary_label: string | null
  channel: string | null
  confidence: number | null
}

export interface NewSender {
  sender: string
  email_count: number
}

export interface SenderProfile {
  sender_email: string
  trust_tier: string
  email_count: number
  total_approved: number
  total_rejected: number
  approval_rate: number
  auto_action_eligible: number | boolean
}
