import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import type { ReactNode } from 'react'

type ResumeRevealPanelProps = {
  visible: boolean
  onToggle: () => void
  label: string
  children: ReactNode
}

/** Reusable show/hide shell for resume preview panels. */
export function ResumeRevealPanel({ visible, onToggle, label, children }: ResumeRevealPanelProps) {
  return (
    <div className="space-y-2">
      <Button type="button" variant="secondary" size="sm" onClick={onToggle}>
        {label}
      </Button>
      {visible ? (
        <Card>
          <CardContent className="p-3">{children}</CardContent>
        </Card>
      ) : null}
    </div>
  )
}
