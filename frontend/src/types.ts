/** Shared API contract types mirroring the backend response models. */

export type SourceType = 'statute' | 'user_document'

export interface Source {
  citation: string
  source_type?: SourceType
  act?: string
  act_short?: string
  section_number?: string
  section_title?: string | null
  text: string
  page_start?: number
  page_end?: number
  chunk_id?: string
  document_id?: string
  source_uri?: string
}

export interface DoneEvent {
  /** Retrieval confidence; null on conversational turns (no retrieval ran). */
  confidence: number | null
  refused: boolean
  model: string | null
  citations: string[]
  /** Answer language actually used (D-079); drives TTS voice selection. */
  language?: string
}

export interface ErrorEvent {
  code: string
  message: string
}

export interface DocumentStatus {
  document_id: string
  job_id: string
  filename: string
  status: 'queued' | 'uploaded' | 'processing' | 'parsing' | 'chunking'
  | 'embedding' | 'indexing' | 'ready' | 'failed'
  stages: string[]
  error_code: string | null
  error_message: string | null
  page_count: number | null
  chunk_count: number | null
}

export interface DocumentListItem {
  document_id: string
  filename: string
  status: string
  page_count: number | null
  chunk_count: number | null
  created_at: string
  updated_at: string
}

export interface FormListItem {
  form_number: number
  title: string
  source_page_start: number
  source_page_end: number
  output_filename: string
  byte_size: number
  needs_review: boolean
}

export interface ChatTurnPayload {
  role: 'user' | 'assistant'
  content: string
}
