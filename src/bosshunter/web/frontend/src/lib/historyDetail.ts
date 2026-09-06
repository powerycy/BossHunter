import type { HistoryItem } from '@/hooks/useDashboard'

export interface ParsedHistoryDetail {
  schema: string
  hrQuestion: string
  aiReply: string
  systemReason: string
  pendingHistoryId: number | null
  conversationTail: Array<{ sender: string; text: string; time?: string; kind?: string }>
}

export function parseHistoryDetail(item: HistoryItem): ParsedHistoryDetail {
  if (item.detail_payload) {
    const payloadReply = item.detail_payload.ai_reply || item.detail_payload.manual_reply || ''
    return {
      schema: item.detail_payload.schema || 'unknown',
      hrQuestion: item.detail_payload.hr_question || item.detail_payload.pending_hr_question || '',
      aiReply: item.action === 'resume_failed' ? '' : payloadReply,
      systemReason: item.detail_payload.system_reason || (item.action === 'resume_failed' ? payloadReply : ''),
      pendingHistoryId: typeof item.detail_payload.pending_history_id === 'number' ? item.detail_payload.pending_history_id : null,
      conversationTail: item.detail_payload.conversation_tail || [],
    }
  }

  if (!item.detail) {
    return {
      schema: 'legacy_text',
      hrQuestion: '',
      aiReply: '',
      systemReason: '',
      pendingHistoryId: null,
      conversationTail: [],
    }
  }

  try {
    const parsed = JSON.parse(item.detail)
    if (parsed && typeof parsed === 'object') {
      const payloadReply = parsed.ai_reply || parsed.manual_reply || ''
      return {
        schema: parsed.schema || 'unknown',
        hrQuestion: parsed.hr_question || parsed.pending_hr_question || '',
        aiReply: item.action === 'resume_failed' ? '' : payloadReply,
        systemReason: parsed.system_reason || (item.action === 'resume_failed' ? payloadReply : ''),
        pendingHistoryId: typeof parsed.pending_history_id === 'number' ? parsed.pending_history_id : null,
        conversationTail: Array.isArray(parsed.conversation_tail) ? parsed.conversation_tail : [],
      }
    }
  } catch {
    // Legacy text detail.
  }

  return {
    schema: 'legacy_text',
    hrQuestion: '',
    aiReply: item.action === 'resume_failed' ? '' : item.detail,
    systemReason: item.action === 'resume_failed' ? item.detail : '',
    pendingHistoryId: null,
    conversationTail: [],
  }
}
