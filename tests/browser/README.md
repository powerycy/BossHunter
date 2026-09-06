# 发送反馈浏览器回归

先在 `src/bosshunter/web/frontend` 运行 `npm run build`。随后在仓库根目录、使用已安装的 Playwright 和 Chrome 运行：

```sh
node tests/browser/retry-feedback.cjs
```

`PLAYWRIGHT_MODULE` 可指向已有 Playwright 模块目录，`BROWSER_CHANNEL` 默认 `chrome`。测试只使用临时本地 HTTP 服务和合成岗位，阻止所有外部请求，不会向招聘平台发送信息。覆盖正常发送返回 409 时提示可见、最后一条失败重试入队并消失后成功提示仍可见，以及等待期间禁用按钮。断言失败时返回非零状态；截图和 JSON 写入 `output/playwright`。
