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

  const commitInput = () => {
    const tag = input.trim()
    if (!tag) return
    if (onAdd) {
      onAdd(tag)
    } else if (!value.includes(tag)) {
      onChange([...value, tag])
    }
    setInput('')
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      // 中文输入法组合阶段按回车不提交，避免把拼音当成标签
      if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return
      e.preventDefault()
      commitInput()
    } else if (e.key === 'Backspace' && !input && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  const handleBlur = () => {
    commitInput()
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
        onBlur={handleBlur}
        placeholder={value.length === 0 ? placeholder : ''}
        className="flex-1 min-w-[80px] bg-transparent text-sm text-foreground placeholder:text-muted/60 outline-none"
      />
    </div>
  )
}
