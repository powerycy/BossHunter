import { cn } from '@/lib/utils'

interface SliderProps {
  value: number
  onChange: (value: number) => void
  min?: number
  max?: number
  step?: number
  className?: string
}

export function Slider({ value, onChange, min = 0, max = 100, step = 1, className }: SliderProps) {
  const pct = ((value - min) / (max - min)) * 100

  return (
    <div className={cn('relative flex items-center', className)}>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="w-full h-2 bg-zinc-700 rounded-full appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:cursor-pointer"
        style={{
          background: `linear-gradient(to right, #3b82f6 0%, #3b82f6 ${pct}%, #3f3f46 ${pct}%, #3f3f46 100%)`
        }}
      />
      <span className="ml-3 text-sm text-zinc-300 min-w-[3ch] text-right">{value}</span>
    </div>
  )
}
