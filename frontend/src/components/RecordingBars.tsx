/**
 * Live recording visualizer: equalizer bars driven by an AnalyserNode.
 *
 * Reads frequency data on rAF and renders animated bars so the user sees
 * their own voice while recording (Gemini/ChatGPT-style feedback). Degrades
 * to a gentle idle pulse when no analyser is available.
 */

import { useEffect, useRef } from 'react'

const BAR_COUNT = 5

export function RecordingBars({ analyser }: { analyser: AnalyserNode | null }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const context = canvas.getContext('2d')
    if (!context) return

    let frame = 0
    let idlePhase = 0
    let smoothed = new Array<number>(BAR_COUNT).fill(0)

    const draw = () => {
      const { width, height } = canvas
      context.clearRect(0, 0, width, height)
      const levels: number[] = []
      if (analyser) {
        const data = new Uint8Array(analyser.frequencyBinCount)
        analyser.getByteFrequencyData(data)
        // Split the low/mid spectrum (voice band) evenly across the bars.
        const usable = Math.floor(data.length * 0.6)
        for (let bar = 0; bar < BAR_COUNT; bar += 1) {
          const from = Math.floor((usable * bar) / BAR_COUNT)
          const to = Math.max(from + 1, Math.floor((usable * (bar + 1)) / BAR_COUNT))
          let sum = 0
          for (let i = from; i < to; i += 1) sum += data[i]
          levels.push(sum / (to - from) / 255)
        }
      } else {
        idlePhase += 0.05
        for (let bar = 0; bar < BAR_COUNT; bar += 1) {
          levels.push(0.25 + 0.15 * Math.sin(idlePhase + bar * 0.9))
        }
      }

      // Ease each bar so motion reads as smooth, not strobing.
      smoothed = smoothed.map((previous, index) => previous + (levels[index] - previous) * 0.5)

      const barWidth = width / (BAR_COUNT * 2 - 1)
      smoothed.forEach((level, index) => {
        const barHeight = Math.max(3, level * height)
        const x = index * barWidth * 2
        const y = (height - barHeight) / 2
        context.fillStyle = '#ef4444'
        context.beginPath()
        context.roundRect(x, y, barWidth, barHeight, barWidth / 2)
        context.fill()
      })

      frame = window.requestAnimationFrame(draw)
    }

    frame = window.requestAnimationFrame(draw)
    return () => window.cancelAnimationFrame(frame)
  }, [analyser])

  return (
    <canvas
      ref={canvasRef}
      width={72}
      height={20}
      aria-hidden="true"
      className="h-5 w-[72px] shrink-0"
    />
  )
}
