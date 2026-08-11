import { useState } from 'react'
import { useConfig } from '@/hooks/useConfig'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Card, CardContent } from '@/components/ui/card'
import { Save, RotateCcw } from 'lucide-react'

const AI_SERVICES = {
  anthropic: { label: 'Claude / Anthropic', provider: 'anthropic', baseUrl: '', defaultModel: 'claude-sonnet-4-6', keyEnv: 'ANTHROPIC_API_KEY' },
  deepseek: { label: 'DeepSeek', provider: 'openai_compatible', baseUrl: 'https://api.deepseek.com', defaultModel: '', keyEnv: 'DEEPSEEK_API_KEY' },
  doubao: { label: '豆包 / 火山方舟', provider: 'openai_compatible', baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', defaultModel: '', keyEnv: 'ARK_API_KEY' },
  lulucoding: { label: 'LuluCoding', provider: 'openai_compatible', baseUrl: 'https://api.lulucoding.com/v1', defaultModel: '', keyEnv: 'LULUCODING_API_KEY' },
  custom: { label: '其他 OpenAI 兼容接口', provider: 'openai_compatible', baseUrl: '', defaultModel: '', keyEnv: 'OPENAI_API_KEY' },
} as const

type AiService = keyof typeof AI_SERVICES

export default function AiSettingsForm() {
  const { config, loading, saving, dirty, error, message, updateConfig, saveConfig, resetConfig } = useConfig()
  const [basicTest, setBasicTest] = useState<{ loading: boolean; message?: string; ok?: boolean; models?: Array<{ id: string }> }>({ loading: false })
  const [advancedTest, setAdvancedTest] = useState<{ loading: boolean; message?: string; stages?: Array<{ id: string; message: string; elapsed_ms?: number }> }>({ loading: false })

  if (loading || !config) return <div className="py-12 text-center text-sm text-muted">配置加载中...</div>

  const service = (config.ai?.service || (config.ai?.provider === 'openai_compatible' ? 'custom' : 'anthropic')) as AiService
  const preset = AI_SERVICES[service] || AI_SERVICES.custom

  const changeService = (next: AiService) => {
    if (next === service) return
    if ((config.ai?.api_key || config.ai?.api_key_masked || config.ai?.auth_token_masked) && !window.confirm('切换 AI 服务商会清除当前保存的 AI 凭证，是否继续？')) return
    const nextPreset = AI_SERVICES[next]
    updateConfig('ai.service', next)
    updateConfig('ai.provider', nextPreset.provider)
    updateConfig('ai.base_url', nextPreset.baseUrl)
    updateConfig('ai.model', nextPreset.defaultModel)
    updateConfig('ai.api_key', '')
    updateConfig('ai.api_key_masked', '')
    updateConfig('ai.auth_token_masked', '')
    updateConfig('ai.clear_credentials', true)
    setBasicTest({ loading: false })
  }

  const testBasic = async () => {
    if (dirty) {
      setBasicTest({ loading: false, ok: false, message: '请先保存当前配置，再测试 AI 连接。' })
      return
    }
    setBasicTest({ loading: true })
    try {
      const response = await fetch('/api/diagnostics/ai', { cache: 'no-store' })
      const data = await response.json()
      const check = Array.isArray(data.checks) ? data.checks[0] : null
      setBasicTest({ loading: false, ok: Boolean(response.ok && data.ok), message: check ? `${check.message}：${check.detail}` : (data.messages?.[0] || 'AI 接口未返回检测结果'), models: Array.isArray(data.models) ? data.models : [] })
    } catch {
      setBasicTest({ loading: false, ok: false, message: '无法连接本地检测接口，请确认后端正在运行。' })
    }
  }

  const testAdvanced = async () => {
    if (dirty) {
      setAdvancedTest({ loading: false, message: '请先保存当前配置。' })
      return
    }
    if (!window.confirm('高级实际测试会发送一次极短的虚拟请求并产生少量 Token，确认继续吗？')) return
    setAdvancedTest({ loading: true })
    try {
      const response = await fetch('/api/diagnostics/ai/advanced', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmed: true }) })
      const data = await response.json()
      setAdvancedTest({ loading: false, message: data.message || (data.ok ? '高级测试通过' : '高级测试未通过'), stages: data.stages || [] })
    } catch {
      setAdvancedTest({ loading: false, message: '高级测试请求失败' })
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-black">AI 设置</h2>
          <p className="mt-1 text-sm text-muted">凭据只在本地保存和使用；基础检测只读取模型列表，不产生生成 Token。</p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={resetConfig}><RotateCcw className="mr-1 h-3 w-3" />重置</Button>
          <Button size="sm" onClick={saveConfig} disabled={saving || !dirty}><Save className="mr-1 h-3 w-3" />{saving ? '保存中...' : '保存'}</Button>
        </div>
      </div>
      {error && <div className="rounded-2xl bg-red-50 px-4 py-3 text-sm text-danger">{error}</div>}
      {message && <div className={`rounded-2xl px-4 py-3 text-sm ${message.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-danger'}`}>{message.text}</div>}
      {dirty && <div className="rounded-2xl bg-amber-50 px-4 py-3 text-xs text-amber-700">有未保存的 AI 配置更改，保存后才能进行连接检测。</div>}

      <Card><CardContent className="space-y-4 p-5">
        <Field label="服务商"><Select value={service} onChange={event => changeService(event.target.value as AiService)}>{Object.entries(AI_SERVICES).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}</Select><p className="mt-1 text-xs text-muted">默认环境变量：{preset.keyEnv}，不会显示变量内容。</p></Field>
        <Field label="模型名称"><Input value={config.ai?.model || ''} onChange={event => updateConfig('ai.model', event.target.value)} placeholder="填写服务商当前支持的模型 ID" /></Field>
        <Field label="API Key"><Input type="password" value={config.ai?.api_key || ''} onChange={event => updateConfig('ai.api_key', event.target.value)} placeholder={config.ai?.api_key_masked || '也可通过环境变量设置'} /></Field>
        <Field label="Base URL"><Input value={config.ai?.base_url || ''} onChange={event => updateConfig('ai.base_url', event.target.value)} placeholder="留空使用默认" /></Field>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Thinking 模式"><Select value={config.ai?.thinking || 'auto'} onChange={event => updateConfig('ai.thinking', event.target.value)}><option value="auto">自动兼容（推荐）</option><option value="disabled">强制关闭</option><option value="enabled">强制开启</option><option value="off">不发送参数</option></Select></Field>
          <Field label="Thinking 预算 Token"><Input type="number" value={config.ai?.thinking_budget || 2048} onChange={event => updateConfig('ai.thinking_budget', Number(event.target.value))} min={1024} max={32768} disabled={(config.ai?.thinking || 'auto') !== 'enabled'} /></Field>
        </div>
        <Field label="AI 请求超时（秒）"><Input type="number" value={config.ai?.timeout_seconds || 180} onChange={event => updateConfig('ai.timeout_seconds', Number(event.target.value))} min={5} max={600} /></Field>
      </CardContent></Card>

      <Card><CardContent className="space-y-4 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><div className="font-black">基础连接检测</div><p className="mt-1 text-xs text-muted">只请求 /models，不调用评分、招呼语或其他生成接口。</p></div><Button variant="secondary" size="sm" onClick={testBasic} disabled={basicTest.loading}>{basicTest.loading ? '检测中...' : '测试连接'}</Button></div>
        {basicTest.message && <p className={`rounded-lg px-3 py-2 text-xs ${basicTest.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-danger'}`}>{basicTest.message}</p>}
        {basicTest.models?.length ? <div className="rounded-xl border border-card-border p-3"><div className="text-xs font-black">可用模型（选择后不会自动保存）</div><div className="mt-2 flex flex-wrap gap-2">{basicTest.models.map(model => <button key={model.id} type="button" onClick={() => updateConfig('ai.model', model.id)} className={`rounded-lg border px-2 py-1 text-xs ${config.ai?.model === model.id ? 'border-primary bg-primary/10 text-primary' : 'border-card-border text-muted'}`}>{model.id}</button>)}</div></div> : null}
        <div className="border-t border-card-border pt-4"><div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted">高级实际测试会消耗少量 Token，只使用虚拟短输入。</p><Button variant="secondary" size="sm" onClick={testAdvanced} disabled={advancedTest.loading}>{advancedTest.loading ? '高级测试中...' : '高级实际测试'}</Button></div>{advancedTest.message && <p className="mt-2 rounded-lg bg-[#FFF0E5] px-3 py-2 text-xs text-primary">{advancedTest.message}</p>}{advancedTest.stages?.length ? <div className="mt-2 grid gap-1 text-[11px] text-muted">{advancedTest.stages.map(stage => <div key={stage.id}>{stage.id}：{stage.message}{stage.elapsed_ms !== undefined ? ` · ${stage.elapsed_ms}ms` : ''}</div>)}</div> : null}</div>
      </CardContent></Card>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="mb-1.5 block text-xs text-foreground">{label}</label>{children}</div>
}
