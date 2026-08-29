# BossHunter 常见问题

## 会被封号吗？

存在风险。低频、随机间隔、时间窗口和人工确认可以降低风险，但不能保证账号绝对安全。招聘平台的规则和检测逻辑也可能变化，请仅用于个人求职并采用保守配置。

## 支持哪些招聘平台？

- BOSS 直聘：采集、AI 评分、人工确认后的低频发送和回复监听。
- 智联招聘：只读采集、AI 评分和招呼语准备；在原平台手动投递。
- 前程无忧 51job：只读采集、AI 评分和招呼语准备；在原平台手动投递。

检测到验证码、频率限制、登录墙或无法识别的页面时，BossHunter 不会尝试绕过。

## 支持哪些 AI 服务？

支持官方 Anthropic、Anthropic Messages 兼容接口，以及 OpenAI Chat Completions 兼容接口。DeepSeek、豆包等服务可在本地配置面板中选择；兼容服务需要填写其 Base URL、API Key 和模型名。

## 简历支持什么格式？

支持 Markdown（`.md`）、Word（`.docx`）和带文字层的 PDF（`.pdf`）。Word 与 PDF 会在本地转换后使用。加密、损坏、扫描版或无文字层 PDF 会给出提示，扫描版请先 OCR。旧版二进制 `.doc` 暂不支持。

## 为什么需要 Chrome 远程调试？

项目通过 Chrome DevTools Protocol 连接你已登录的浏览器，因此不需要保存招聘平台账号密码。BossHunter 不会替你启动或登录平台；请先开启远程调试，并在同一个 Chrome 窗口完成登录。

## 为什么 `bosshunter connect` 检测失败？

请依次确认：

1. 使用的是 Google Chrome。
2. 已开启远程调试。
3. 招聘平台登录在可远程控制的同一个 Chrome 窗口。
4. Chrome 窗口仍保持开启。

详细步骤见 [完整上手指南](QUICKSTART.md)。
