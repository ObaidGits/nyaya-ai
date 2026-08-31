/**
 * Corpus management panel: shows the active BNS corpus manifest and the
 * replacement upload. Replacement requires explicit confirmation — the
 * backend re-validates content, so a BNSS upload is rejected as BNS.
 */

import { useEffect, useRef, useState } from 'react'
import { fetchCorpus, uploadCorpus, type CorpusManifest } from '../../lib/admin'
import { toast } from '../../lib/toast'

function ManifestTable({ manifest }: { manifest: CorpusManifest }) {
  const rows: [string, string][] = [
    ['Act', manifest.act],
    ['Short name', manifest.act_short],
    ['Source file', manifest.filename],
    ['SHA-256', manifest.sha256],
    ['Pages', String(manifest.pages)],
    ['Sections', String(manifest.sections)],
    ['Chunks', String(manifest.chunks)],
    ['Ingested at', new Date(manifest.ingested_at).toLocaleString()],
  ]
  return (
    <dl className="mt-3 grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="flex min-w-0 gap-2">
          <dt className="w-28 shrink-0 text-ink-500 dark:text-ink-400">{label}</dt>
          <dd className="min-w-0 truncate font-mono" title={value}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export function CorpusPanel({ onChanged }: { onChanged?: () => void }) {
  const [manifest, setManifest] = useState<CorpusManifest | null>(null)
  const [detail, setDetail] = useState<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const reload = () => {
    fetchCorpus().then((info) => {
      if ('sha256' in info && info.sha256) {
        setManifest(info as CorpusManifest)
        setDetail(null)
      } else {
        setManifest(null)
        setDetail('detail' in info ? info.detail : 'No manifest.')
      }
    })
  }
  useEffect(reload, [])

  const submit = async () => {
    if (!file) return
    setUploading(true)
    try {
      const result = await uploadCorpus(file)
      toast.info('New corpus validated and activated.')
      setManifest(result.corpus)
      setFile(null)
      setConfirming(false)
      if (fileInput.current) fileInput.current.value = ''
      onChanged?.()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Upload failed.')
      reload()
    } finally {
      setUploading(false)
    }
  }

  return (
    <section
      aria-labelledby="sec-corpus"
      className="rounded-xl border border-ink-200 bg-white p-5 shadow-sm dark:border-ink-800 dark:bg-ink-900"
    >
      <h2 id="sec-corpus" className="font-serif text-lg font-bold">
        Legal corpus
      </h2>
      <p className="mt-1 text-sm text-ink-500 dark:text-ink-400">
        The active corpus is content-validated: only the Bharatiya Nyaya Sanhita, 2023 is
        accepted. A replacement activates only after full re-ingestion and verification —
        a failed upload never touches the active corpus.
      </p>

      {manifest ? (
        <ManifestTable manifest={manifest} />
      ) : (
        <p className="mt-3 text-sm text-ink-500 dark:text-ink-400">{detail ?? 'No manifest.'}</p>
      )}

      <div className="mt-4 border-t border-ink-100 pt-4 dark:border-ink-800">
        <label className="block text-sm font-medium" htmlFor="corpus-pdf">
          Replace corpus (Gazette PDF)
        </label>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            ref={fileInput}
            id="corpus-pdf"
            type="file"
            accept="application/pdf"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null)
              setConfirming(false)
            }}
            className="text-sm"
          />
          <button
            type="button"
            disabled={!file || uploading}
            onClick={() => setConfirming(true)}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50 dark:bg-red-500 dark:hover:bg-red-400"
          >
            {uploading ? 'Ingesting…' : 'Replace corpus'}
          </button>
        </div>
        {confirming && file && (
          <div role="alertdialog" aria-labelledby="corpus-confirm-title" className="mt-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm dark:border-red-900 dark:bg-red-950/50">
            <p id="corpus-confirm-title" className="font-medium text-red-800 dark:text-red-200">
              Replace the active legal corpus?
            </p>
            <p className="mt-1 text-red-700 dark:text-red-300">
              {file.name} will be validated against the BNS content signature, re-indexed, and
              verified before activation. This cannot be undone once activated.
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={submit}
                disabled={uploading}
                className="rounded-lg bg-red-600 px-3 py-1.5 font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {uploading ? 'Ingesting…' : 'Yes, replace'}
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="rounded-lg border border-red-300 px-3 py-1.5 text-red-700 hover:bg-red-100 dark:border-red-800 dark:text-red-300"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
