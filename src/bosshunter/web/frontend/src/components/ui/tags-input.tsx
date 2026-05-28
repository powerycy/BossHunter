import { useState, KeyboardEvent } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface TagsInputProps {
  value: string[]
  onChange: (tags: string[]) => void
  placeholder?: string
  className?: string
}

export function TagsInput({ value, onChange, placeholder = '输入后按回车添加', className }: TagsInputProps) {
  const [input, setInput] = useState('')

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && input.trim()) {
      e.preventDefault()
      if (!value.includes(input.trim())) {
        onChange([...value, input.trim()])
      }
      setInput('')
    } else if (e.key === 'Backspace' && !input && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  const removeTag = (index: number) => {
    onChange(value.filter((_, i) => i !== index))
  }

  return (
    <div className={cn(
      'flex flex-wrap gap-1.5 min-h-[36px] p-2 rounded-md border border-zinc-700 bg-zinc-800 focus-within:ring-2 focus-within:ring-primary/50 focus-within:border-primary',
      className
    )}>
      {value.map((tag, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 rounded-md bg-zinc-700 px-2 py-0.5 text-xs text-zinc-200"
        >
          {tag}
          <button
            type="button"
            onClick={() => removeTag(i)}
            className="text-zinc-400 hover:text-white"
          >
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}
      <input
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={value.length === 0 ? placeholder : ''}
        className="flex-1 min-w-[80px] bg-transparent text-sm text-zinc-100 placeholder:text-zinc-500 outline-none"
      />
    </div>
  )
}
