import { useState, useEffect, useCallback } from 'react'

export function useConfig() {
  const [config, setConfig] = useState<Record<string, any> | null>(null)
  const [schema, setSchema] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const fetchConfig = async () => {
    try {
      setError(null)
      const [configRes, schemaRes] = await Promise.all([
        fetch('/api/config'),
        fetch('/api/config/schema'),
      ])
      if (!configRes.ok) throw new Error('配置接口请求失败')
      if (!schemaRes.ok) throw new Error('配置结构接口请求失败')
      const configData = await configRes.json()
      const schemaData = await schemaRes.json()
      setConfig(configData)
      setSchema(schemaData)
      setDirty(false)
    } catch (err) {
      console.error('Failed to fetch config:', err)
      setConfig(null)
      setError(err instanceof Error ? err.message : '配置加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchConfig()
  }, [])

  const updateConfig = useCallback((path: string, value: any) => {
    setConfig(prev => {
      if (!prev) return prev
      const keys = path.split('.')
      const next = JSON.parse(JSON.stringify(prev))
      let obj = next
      for (let i = 0; i < keys.length - 1; i++) {
        if (!obj[keys[i]]) obj[keys[i]] = {}
        obj = obj[keys[i]]
      }
      obj[keys[keys.length - 1]] = value
      return next
    })
    setDirty(true)
  }, [])

  const saveConfig = async () => {
    if (!config) return
    setSaving(true)
    setMessage(null)
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      const data = await res.json()
      if (data.success) {
        await fetchConfig()
        setMessage({ type: 'success', text: '配置已保存' })
        setDirty(false)
      } else {
        setMessage({ type: 'error', text: data.error || '保存失败' })
      }
    } catch (err) {
      setMessage({ type: 'error', text: '网络错误' })
    } finally {
      setSaving(false)
      setTimeout(() => setMessage(null), 3000)
    }
  }

  const resetConfig = () => {
    setLoading(true)
    fetchConfig()
  }

  return { config, schema, loading, saving, dirty, error, message, updateConfig, saveConfig, resetConfig }
}
