import { useState, KeyboardEvent } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface TagsInputProps {
  value: string[]
  onChange: (tags: string[]) => void
  placeholder?: string
  className?: string
  onAdd?: (tag: string) => void
}

export function TagsInput({ value, onChange, placeholder = '输入后按回车添加', className, onAdd }: TagsInputProps) {
  const [input, setInput] = useState('')

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && input.trim() && !e.nativeEvent.isComposing) {
      e.preventDefault()
      const tags = input.split(/[,，、;；]/).map(tag => tag.trim()).filter(Boolean)
      if (onAdd) {
        tags.forEach(onAdd)
      } else {
        onChange([...new Set([...value, ...tags])])
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
      'flex flex-wrap gap-1.5 min-h-[36px] p-2 rounded-md border border-card-border bg-white focus-within:ring-2 focus-within:ring-primary/30 focus-within:border-primary',
      className
    )}>
      {value.map((tag, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 rounded-md bg-[#FFF0E5] px-2 py-0.5 text-xs font-bold text-primary"
        >
          {tag}
          <button
            type="button"
            onClick={() => removeTag(i)}
            className="text-primary/70 hover:text-primary"
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
        className="flex-1 min-w-[80px] bg-transparent text-sm text-foreground placeholder:text-muted/60 outline-none"
      />
    </div>
  )
}
