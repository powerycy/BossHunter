import { Search, Bot, MessageSquare, CheckCircle, Send, Eye } from 'lucide-react'

const steps = [
  { icon: Search, label: '采集', desc: '搜索岗位' },
  { icon: Bot, label: 'AI评分', desc: '匹配打分' },
  { icon: MessageSquare, label: '招呼语', desc: '个性生成' },
  { icon: CheckCircle, label: '人工确认', desc: '审核通过' },
  { icon: Send, label: '发送', desc: '自动投递' },
  { icon: Eye, label: '监控', desc: '跟进回复' },
]

export function PipelineFlow() {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-6">
      <h3 className="text-sm font-medium text-zinc-400 mb-4">BossHunter 自动求职流程</h3>
      <div className="flex items-center justify-between">
        {steps.map((step, i) => (
          <div key={step.label} className="flex items-center">
            <div className="flex flex-col items-center">
              <div className="w-12 h-12 rounded-lg bg-zinc-800 border border-zinc-700 flex items-center justify-center mb-2 hover:border-primary/50 transition-colors">
                <step.icon className="w-5 h-5 text-primary" />
              </div>
              <span className="text-xs font-medium text-zinc-200">{step.label}</span>
              <span className="text-[10px] text-zinc-500 mt-0.5">{step.desc}</span>
            </div>
            {i < steps.length - 1 && (
              <div className="w-8 h-px bg-gradient-to-r from-zinc-600 to-zinc-700 mx-2 mb-6" />
            )}
          </div>
        ))}
      </div>
      <p className="text-xs text-zinc-500 mt-4">
        每个步骤都可独立运行，也可 <code className="text-zinc-300">bosshunter run</code> 一键全流程执行
      </p>
    </div>
  )
}
