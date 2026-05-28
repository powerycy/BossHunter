import { cn } from '@/lib/utils'
import { forwardRef, SelectHTMLAttributes } from 'react'

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select
      className={cn(
        'flex h-9 w-full rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary disabled:cursor-not-allowed disabled:opacity-50 appearance-none',
        className
      )}
      ref={ref}
      {...props}
    >
      {children}
    </select>
  )
)
Select.displayName = 'Select'
