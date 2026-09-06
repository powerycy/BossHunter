# BossHunter 交互结构图

[在线打开](https://shengjidaguai-china.github.io/BossHunter/architecture/)。也可以下载 `bosshunter.architecture.html` 后在浏览器中离线打开。

- `bosshunter.architecture.json`：可编辑源文件。
- `bosshunter.light.png` / `bosshunter.dark.png`：README 的深浅主题预览。
- `ARCHIFY-LICENSE.txt`：[Archify](https://github.com/yuppiez99999/archify-) 渲染器的 MIT 许可。

这是核心模块职责与主要调用关系图。任务编排概括 Web 后台任务和 CLI 流程；人工确认是由界面或 CLI 提供的逻辑环节。重复的数据库读写以及监测对 AI 的调用收录在图下方说明中。

依据 `src/bosshunter/web/server.py`、`web/tasks.py`、`pipeline.py`、`collection/orchestrator.py`、`collection/capabilities.py`、`ai/`、`executor/`、`browser/client.py`、`browser/runtime/cdp-proxy.mjs` 和 `db.py` 绘制。

已通过 Archify 的 9 项 showcase 检查，0 错误、0 警告；1440×900、1600×1000、1920×1080、2048×1320 均无页面溢出，并已检查深浅主题截图。

## 发布

`.github/workflows/pages-architecture.yml` 将交互图发布到 `/BossHunter/architecture/`。现有演示网站使用其上次成功部署的固定源码版本 `e32ef7e98d5b4519d6a2459c31dde1953d040bcf` 重建，保留原站点入口与功能，避免将当前本地工作台发布成无后端网站。
