# BossHunter 完整上手指南

本指南适合第一次安装和运行 BossHunter。首页只保留最短启动路径，平台登录、浏览器连接和排错细节统一放在这里。

## 1. 准备环境

| 依赖 | 版本 | 用途 |
|---|---|---|
| Python | 3.10+ | 核心运行时 |
| Node.js | 22+ | 本地 Browser Runtime / CDP 代理 |
| Google Chrome | 最新稳定版 | 连接已登录的招聘平台 |
| AI API Key | — | Anthropic 或 OpenAI 兼容接口 |

自动化操作招聘平台存在账号限制或封禁风险。请仅用于个人求职，保持低频，并遵守平台规则。

## 2. 安装

```bash
git clone https://github.com/shengjidaguai-china/BossHunter.git
cd BossHunter
pip install -e .
```

仅在需要 `xhtml2pdf` 备用渲染时安装 PDF 可选依赖：

```bash
pip install -e ".[pdf]"
```

## 3. 开启 Chrome 远程调试

推荐在 Chrome 地址栏打开 `chrome://inspect/#remote-debugging`，启用 **Allow remote debugging**。

也可以使用独立用户目录启动 Chrome：

```bash
# Windows
chrome.exe --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\BossHunterChrome"

# macOS
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/.bosshunter-chrome"

# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.bosshunter-chrome"
```

使用启动参数时会打开一个独立 Chrome 窗口。请在这个窗口中登录要使用的招聘平台，并在任务期间保持窗口开启；其他 Chrome 窗口的登录状态不会自动复用。

## 4. 完成本地配置

```bash
bosshunter web
```

浏览器会打开 `http://127.0.0.1:8686`。请在本地面板完成：

1. 上传自己的 Markdown（`.md`）或 Word（`.docx`）简历，不要继续使用示例简历。
2. 设置搜索关键词、目标城市、评分阈值、发送频率和时间窗口。
3. 在“AI 设置”中选择服务商，填写服务商提供的 API Key 和模型名称。
4. 保存配置。

API Key 只应在本地面板输入，不要粘贴到 Issue、聊天记录或提交文件中。更多字段说明见 [配置指南](CONFIGURATION.md)。

## 5. 检查连接

```bash
bosshunter ai-status
bosshunter connect
```

- `ai-status` 安全检查 AI 服务，不显示完整 Key。
- `connect` 只检查 Browser Runtime 和 Chrome 连接，不会替你启动或登录 Chrome。

如果浏览器连接失败，请确认远程调试已开启，并且招聘平台是在同一个可控制的 Chrome 窗口中登录。

## 6. 开始运行

确认简历、AI 和 Chrome 均已就绪后运行：

```bash
bosshunter run
```

完整流程为：采集岗位 → AI 评分 → 人工确认投递清单 → 生成招呼语 → 低频发送 → 监听回复。

只有 BOSS 直聘支持确认后的低频发送和监听。智联招聘、前程无忧 51job 仅支持只读采集和 AI 处理；请打开原平台链接手动投递，再回到岗位池标记“已发送”。

可在工作台停止任务；命令行模式按 `Ctrl+C` 停止。全部命令见 [CLI 命令](CLI.md)。

## 安全边界

- 所有投递必须经过人工确认。
- 仅在配置的时间窗口内发送，并受随机间隔和每日上限约束。
- 检测到验证码、频率限制、登录墙或未知页面时停止，不尝试绕过。
- 即使采用保守策略，也无法保证账号绝对安全，请自行评估风险。
