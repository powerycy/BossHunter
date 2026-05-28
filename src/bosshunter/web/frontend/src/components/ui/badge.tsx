import { cn } from '@/lib/utils'
import { cva, type VariantProps } from 'class-variance-authority'
import { HTMLAttributes } from 'react'

const badgeVariants = cva(
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium border',
  {
    variants: {
      variant: {
        default: 'bg-zinc-600/20 text-zinc-300 border-zinc-600/30',
        pending: 'bg-zinc-600/20 text-zinc-300 border-zinc-600/30',
        scored: 'bg-blue-600/20 text-blue-400 border-blue-600/30',
        approved: 'bg-amber-600/20 text-amber-400 border-amber-600/30',
        sent: 'bg-green-600/20 text-green-400 border-green-600/30',
        replied: 'bg-emerald-600/20 text-emerald-300 border-emerald-600/30',
        resume_sent: 'bg-purple-600/20 text-purple-400 border-purple-600/30',
        rejected: 'bg-red-600/20 text-red-400 border-red-600/30',
        error: 'bg-red-600/20 text-red-400 border-red-600/30',
        filtered: 'bg-zinc-700/20 text-zinc-500 border-zinc-700/30',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
)

export interface BadgeProps extends HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}
