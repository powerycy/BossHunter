import { useState, KeyboardEvent, FocusEvent } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface TagsInputProps {
  value: string[]
  onChange: (tags: string[]) => void
  placeholder?: string
  className?: string
  onAdd?: (tag: string) => void
}

function splitTags(raw: string): string[] {
  // 支持中文顿号、英文逗号、中文逗号、分号作为分隔符
  return raw
    .split(/[、,，;；]/)
    .map(tag => tag.trim())
    .filter(Boolean)
}

export function TagsInput({ value, onChange, placeholder = '输入后按回车添加', className, onAdd }: TagsInputProps) {
  const [input, setInput] = useState('')

  const commitInput = (raw?: string) => {
    const source = raw ?? input
    if (!source.trim()) return
    if (onAdd) {
      onAdd(source.trim())
    } else {
      const tags = splitTags(source)
      if (tags.length === 0) return
      const next = [...value]
      let changed = false
      for (const tag of tags) {
        if (!next.includes(tag)) {
          next.push(tag)
          changed = true
        }
      }
      if (changed) onChange(next)
    }
    setInput('')
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // 中文输入法组合期间的回车(确认候选词)不当作添加操作
    if (e.nativeEvent.isComposing || e.keyCode === 229) return
    if (e.key === 'Enter') {
      e.preventDefault()
      commitInput()
    } else if (e.key === 'Backspace' && !input && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  const handleBlur = (e: FocusEvent<HTMLInputElement>) => {
    // 失焦时自动提交未添加的输入,避免"输入后直接点保存"丢失
    commitInput(e.target.value)
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
