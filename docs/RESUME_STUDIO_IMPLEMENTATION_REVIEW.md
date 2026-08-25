# Resume Studio 代码实现 Review 指南

本文用于审查「根据个人技术文档/作品材料自动整理主简历」功能。内容对应 `codex/resume-studio` 分支，不是用户操作手册。

上游跟踪 Issue：[#60 从技术文档和作品材料生成可追溯主简历](https://github.com/powerycy/BossHunter/issues/60)

## 1. 功能目标

现有 BossHunter 已经支持：

```text
主简历 + 岗位 JD -> 岗位定制简历
```

本次新增的是上游能力：

```text
个人技术文档/作品材料
    -> 文本标准化
    -> AI 候选事实抽取
    -> 用户逐条审核
    -> 已确认事实库
    -> 主简历草稿与版本
    -> 用户明确启用
    -> 现有岗位定制简历流程
```

核心设计目标是“可追溯优先于自动化”：AI 只能提出候选事实，不能绕过人工确认直接改变主简历。

## 2. 本次范围

已实现：

- 多材料上传：`.md`、`.docx`、带文字层的 `.pdf`；
- 单文件 10 MB 限制，转换后文本 120000 字符限制；
- 文本标准化和 SHA-256 内容去重；
- 分片 AI 事实抽取；
- 每条事实记录来源文件、原文证据和置信度；
- 待审核、已接受、已拒绝三态审核；
- 从已接受事实生成结构化主简历草稿；
- 主简历 Markdown 版本、下载、预览和显式启用；
- 防止删除已被任何主简历版本引用的材料；
- 旧单简历上传 API 保持兼容。

未实现：

- 任意网页/作品集 URL 抓取；
- 扫描版 PDF OCR；
- 自动把生成结果上传到招聘平台；
- 自动启用 AI 生成的主简历；
- 主简历富文本编辑器或 PDF 模板设计；
- 多用户或云同步。

## 3. 代码结构

### 3.1 领域层

`src/bosshunter/resume_builder/documents.py`

- 原 `web/resume_upload.py` 的通用文档转换实现下沉到这里；
- 负责 Unicode 文件名清理、扩展名校验、DOCX XML 提取和 PDF 文字层提取；
- Markdown 必须为 UTF-8，避免后续事实证据无法稳定匹配。

`src/bosshunter/resume_builder/store.py`

- Resume Studio SQLite Repository；
- 不调用 AI，不处理 HTTP；
- 负责材料、事实、版本和版本事实引用关系；
- 重新抽取时只替换待审核/已拒绝候选，已接受事实不会被静默覆盖。

`src/bosshunter/resume_builder/service.py`

- 领域服务和安全边界；
- 负责材料落盘、分片、AI JSON 解析、证据校验、主简历组织和受管材料删除；
- AI 抽取和生成函数允许注入 `call_text`，测试不访问真实外部模型。

### 3.2 数据库

`src/bosshunter/db.py::_init_resume_studio`

新增四张幂等创建的表：

| 表 | 用途 |
| --- | --- |
| `resume_sources` | 材料元数据、哈希、本地路径、标准化正文和处理状态 |
| `resume_facts` | 候选事实、人工修改、证据、置信度和审核状态 |
| `resume_versions` | 主简历 Markdown、文件路径、目标方向和启用状态 |
| `resume_version_facts` | 版本使用的事实 ID，提供追溯和删除保护 |

数据库仍由现有 `get_db()` 初始化，不增加独立迁移命令，兼容项目当前的增量初始化模式。

### 3.3 Web API

`src/bosshunter/web/server.py`

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/resume-studio` | 返回材料、事实、版本工作区 |
| POST | `/api/resume-studio/sources` | 上传并标准化一份材料 |
| POST | `/api/resume-studio/sources/<id>/extract` | 提取或重新提取待审核事实 |
| DELETE | `/api/resume-studio/sources/<id>` | 强确认删除未被版本引用的材料 |
| PATCH | `/api/resume-studio/facts/<id>` | 修改事实并切换审核状态 |
| POST | `/api/resume-studio/compose` | 从已接受事实生成草稿版本 |
| POST | `/api/resume-studio/versions/<id>/activate` | 写入 `profile.resume_path` 并标记启用 |
| GET | `/api/resume-studio/versions/<id>/download` | 下载 Markdown 版本 |

上传接口不会把 `normalized_text` 回显给浏览器；完整材料只保存在本地受管目录和本地 SQLite。

### 3.4 前端

`src/bosshunter/web/frontend/src/pages/ResumeStudioPage.tsx`

页面按四步组织：

1. 批量上传材料；
2. 提取并审核事实；
3. 指定可选目标方向并生成草稿；
4. 预览、下载和确认启用。

路由为 `/resume-studio`，入口位于侧边栏「简历工作室」。前端只维护当前操作状态，持久状态每次操作后重新从后端读取，避免前后端审核状态分叉。

## 4. 关键安全不变量

### 4.1 未审核事实不能进入主简历

生成查询只读取 `resume_facts.status = 'accepted'`。待审核和已拒绝事实不会进入模型上下文。

### 4.2 抽取必须存在原文证据

AI 返回的 `evidence` 必须能在对应文本分片中匹配。无法匹配的候选会被丢弃，不进入待审核列表。

### 4.3 结构化事实不得凭空新增

系统提取和比较以下 token：

- 邮箱；
- HTTP/HTTPS 链接；
- 中国大陆手机号；
- 日期；
- 百分比、人数、金额、时长等量化值。

候选事实或主简历条目包含来源证据无法支持的 token 时，会被拒绝或终止生成。

这不是完整的语义事实证明，因此仍保留人工审核和生成后显式启用。

### 4.4 用户材料只写入受管目录

- 材料：`data/resume_sources/`；
- 主简历版本：`data/resumes/`；
- 数据库：`data/bosshunter.db`。

删除前会解析绝对路径，并要求目标位于 `data/resume_sources/` 内。API 还要求 `confirmed: true`。

### 4.5 已引用材料不可删除

只要某条事实已进入任意主简历版本，来源材料就不能删除。这保证历史版本仍可解释和复核。

### 4.6 启用操作不会删除旧简历

启用只更新 `config.yaml` 中的 `profile.resume_path`，不会覆盖或删除之前的主简历文件。

## 5. AI 输入输出契约

### 5.1 事实抽取

输入：单个材料分片。

输出：

```json
{
  "facts": [
    {
      "category": "项目经历",
      "content": "用于简历的事实表述",
      "evidence": "材料中的原文证据",
      "confidence": 0.9
    }
  ]
}
```

服务端不信任 JSON 内容，会继续执行类别、长度、证据和结构化 token 校验。

### 5.2 主简历组织

输入：仅包含事实 ID、类别和人工确认后的有效内容。

输出：

```json
{
  "sections": [
    {
      "title": "项目经历",
      "items": [
        {
          "text": "简历条目",
          "fact_ids": ["fact-id"]
        }
      ]
    }
  ]
}
```

每个条目必须引用有效事实 ID。服务端根据引用事实再次校验结构化 token，然后由代码确定性渲染 Markdown。

## 6. 兼容性说明

- `bosshunter resume --job-id` 行为未变；
- `/api/resume` 和 `/api/resume/upload` 路径未变；
- `bosshunter.web.resume_upload` 继续导出原有函数和异常类型；
- 不使用简历工作室的用户无需迁移配置；
- 新表通过 `CREATE TABLE IF NOT EXISTS` 增量创建；
- 前端构建产物已同步更新并继续打包进 Python wheel。

## 7. 测试证据

开发分支已执行：

```text
python -m pytest tests/test_resume_builder.py tests/test_resume_studio_api.py -q -p no:cacheprovider
11 passed

python -m pytest tests/test_resume_builder.py tests/test_resume_studio_api.py \
  tests/test_resume_pdf_runtime.py tests/test_web_config_api.py tests/test_web_api_routes.py \
  -q -p no:cacheprovider
90 passed

python -m pytest -q -p no:cacheprovider
316 passed, 11 subtests passed

npm run build
TypeScript check and Vite production build passed

ruff check src/bosshunter/resume_builder tests/test_resume_builder.py tests/test_resume_studio_api.py
All checks passed

git diff --check
passed
```

新增测试覆盖：

- 数据表初始化；
- 材料哈希去重；
- AI 虚构百分比过滤；
- 已接受事实不被重新抽取覆盖；
- 只用已接受事实生成版本；
- 主简历新增无来源数字时失败；
- 删除确认和版本引用保护；
- 上传、审核、生成、启用的 WSGI API 全链路；
- 启用后 `profile.resume_path` 指向真实存在的新版本；
- 旧简历上传/PDF/Web API 回归。

## 8. 建议 Review 顺序

1. `resume_builder/service.py`：先确认安全不变量和 AI 边界；
2. `resume_builder/store.py` 与 `db.py`：确认表结构和状态转换；
3. `web/server.py`：确认 API 状态码、配置写入和错误边界；
4. `ResumeStudioPage.tsx`：确认人工审核和启用交互不会被绕过；
5. 两个新增测试文件：对照关键不变量检查是否都有直接断言；
6. 最后检查 README、构建产物和 PR 文件范围。

## 9. 已知限制和后续建议

- 作品集 URL 目前只作为既有配置中的链接使用，不抓取网页正文；后续若实现，应单独设计 SSRF、重定向、域名和隐私规则。
- 事实一致性目前重点校验结构化 token，普通自然语言事实仍依赖证据匹配、人工审核和最终预览。
- AI 调用当前同步执行；超大材料可能耗时较长，后续可以接入现有 `WorkbenchTaskRunner` 做可取消后台任务。
- 当前项目 CI 没有强制运行完整 pytest；建议独立补充 CI 测试 job，不与本功能 PR 混合扩大范围。
- npm 安装报告现有依赖树有 2 个 moderate 和 3 个 high 漏洞；本次没有运行自动升级，以避免未经审查修改锁文件。

## 10. Review 结论填写区

审查者可在本地副本记录：

```text
[ ] 数据模型可接受
[ ] 证据与人工确认边界可接受
[ ] API 与配置写入可接受
[ ] 前端交互可接受
[ ] 测试覆盖可接受
[ ] 已知限制可接受
[ ] 可以提交上游 PR
```
