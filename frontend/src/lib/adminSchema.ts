/**
 * Admin settings form schema (D-080): declarative field metadata for each
 * console section. Only backend-whitelisted operational settings appear —
 * grounding, citations, refusal, and prompt-injection protection are
 * architectural constants, not switches.
 */

export type FieldKind = 'text' | 'number' | 'select' | 'boolean' | 'secret' | 'provider-select'

export interface SettingField {
  key: string
  label: string
  kind: FieldKind
  help?: string
  placeholder?: string
  min?: number
  max?: number
  step?: number
  options?: { value: string; label: string }[]
  /** Shown only when another field currently holds this value. */
  showWhen?: { key: string; notEquals?: string; equals?: string }
  /** Provider-specific fields resolve their visibility from the provider list. */
  providerKind?: 'ollama' | 'openai-compatible' | 'gemini' | 'none'
}

export interface SettingSection {
  id: string
  title: string
  description: string
  test?: { kind: 'llm' | 'stt' | 'tts'; label: string }
  fields: SettingField[]
}

export const ADMIN_SECTIONS: SettingSection[] = [
  {
    id: 'llm',
    title: 'AI / LLM provider',
    description:
      'Generation provider and model. Ollama is the keyless local default; cloud providers need a server-side API key.',
    test: { kind: 'llm', label: 'Test LLM connection' },
    fields: [
      { key: 'llm_provider', label: 'Provider', kind: 'provider-select' },
      {
        key: 'llm_model',
        label: 'Model',
        kind: 'text',
        placeholder: 'e.g. llama3.1 or gpt-4o-mini',
        help: 'Pick a loaded model or type any model id.',
      },
      {
        key: 'llm_base_url',
        label: 'Base URL',
        kind: 'text',
        placeholder: 'https://api.example.com/v1',
        help: 'Only needed for providers without a fixed API URL; leave blank otherwise.',
      },
      {
        key: 'llm_api_key',
        label: 'API key',
        kind: 'secret',
        showWhen: { key: 'llm_provider', notEquals: 'ollama' },
      },
      { key: 'llm_timeout_seconds', label: 'Timeout (seconds)', kind: 'number', min: 1, max: 300 },
      { key: 'llm_temperature', label: 'Temperature', kind: 'number', min: 0, max: 2, step: 0.1 },
      { key: 'llm_num_predict', label: 'Max output tokens', kind: 'number', min: 1, max: 8192 },
    ],
  },
  {
    id: 'language',
    title: 'Language',
    description:
      'Answer-language defaults. Multilingual grounding and citation behavior are always on — never configurable.',
    fields: [
      {
        key: 'language_detection_backend',
        label: 'Language detection backend',
        kind: 'select',
        options: [
          { value: 'fasttext', label: 'fastText (local)' },
          { value: 'llm', label: 'LLM' },
        ],
      },
    ],
  },
  {
    id: 'speech',
    title: 'Voice (STT / TTS)',
    description:
      'Speech providers. Local models are the default; OpenAI-compatible cloud endpoints are opt-in.',
    test: { kind: 'stt', label: 'Test STT' },
    fields: [
      {
        key: 'speech_stt_provider',
        label: 'STT provider',
        kind: 'select',
        options: [
          { value: 'faster-whisper', label: 'faster-whisper (local, ~1-2 GB RAM)' },
          { value: 'whisper', label: 'Whisper (local, ~2-3 GB RAM)' },
          { value: 'indicconformer', label: 'IndicConformer (local, heavy)' },
          { value: 'browser', label: 'Browser Web Speech (zero server RAM)' },
          { value: 'openai', label: 'OpenAI-compatible (cloud)' },
        ],
      },
      { key: 'speech_stt_model', label: 'STT model', kind: 'text', placeholder: 'small' },
      {
        key: 'speech_stt_base_url',
        label: 'STT base URL',
        kind: 'text',
        showWhen: { key: 'speech_stt_provider', equals: 'openai' },
      },
      {
        key: 'speech_stt_api_key',
        label: 'STT API key',
        kind: 'secret',
        showWhen: { key: 'speech_stt_provider', equals: 'openai' },
      },
      { key: 'speech_stt_device', label: 'STT device', kind: 'text', placeholder: 'cpu' },
      { key: 'speech_stt_auto_languages', label: 'Auto language list', kind: 'text' },
      {
        key: 'speech_tts_provider',
        label: 'TTS provider',
        kind: 'select',
        options: [
          { value: 'piper', label: 'Piper (local, light, default)' },
          { value: 'parler-tts', label: 'Parler-TTS (local, heavy)' },
          { value: 'browser', label: 'Browser speechSynthesis (zero server RAM)' },
          { value: 'openai', label: 'OpenAI-compatible (cloud)' },
      ],
      },
      {
        key: 'speech_tts_model',
        label: 'TTS model',
        kind: 'text',
        placeholder: 'en_US-lessac-medium',
      },
      { key: 'speech_tts_voice', label: 'TTS voice', kind: 'text', placeholder: 'alloy' },
      {
        key: 'speech_tts_base_url',
        label: 'TTS base URL',
        kind: 'text',
        showWhen: { key: 'speech_tts_provider', equals: 'openai' },
      },
      {
        key: 'speech_tts_api_key',
        label: 'TTS API key',
        kind: 'secret',
        showWhen: { key: 'speech_tts_provider', equals: 'openai' },
      },
      { key: 'speech_preload', label: 'Preload models at startup', kind: 'boolean' },
    ],
  },
  {
    id: 'retrieval',
    title: 'Retrieval',
    description:
      'Operational retrieval knobs only. Grounding, citation validation, refusal, and prompt-injection protection cannot be disabled.',
    fields: [
      { key: 'retrieval_dense_top_k', label: 'Dense top-k', kind: 'number', min: 1, max: 50 },
      { key: 'retrieval_sparse_top_k', label: 'Sparse top-k', kind: 'number', min: 1, max: 50 },
      {
        key: 'retrieval_confidence_threshold',
        label: 'Confidence threshold',
        kind: 'number',
        min: 0,
        max: 1,
        step: 0.05,
        help: 'Below this retrieval confidence the assistant refuses instead of guessing.',
      },
    ],
  },
  {
    id: 'rate-limits',
    title: 'Rate limits (requests per minute)',
    description: 'Per-session limits enforced server-side.',
    fields: [
      { key: 'rate_limit_chat_per_minute', label: 'Chat', kind: 'number', min: 1, max: 1000 },
      { key: 'rate_limit_upload_per_minute', label: 'Upload', kind: 'number', min: 1, max: 1000 },
      { key: 'rate_limit_speech_per_minute', label: 'Speech', kind: 'number', min: 1, max: 1000 },
    ],
  },
]

// A field with showWhen renders only when another field matches the rule.
// Example: the LLM base URL and API key are hidden when the provider is Ollama,
// because Ollama needs neither. providerKind is resolved separately in
// SettingsSectionCard from the provider list the backend returns.
export function fieldVisible(
  field: SettingField,
  values: Record<string, string | number | boolean>,
): boolean {
  if (!field.showWhen) return true
  const current = String(values[field.showWhen.key] ?? '')
  if (field.showWhen.equals !== undefined) return current === field.showWhen.equals
  return current !== field.showWhen.notEquals
}
