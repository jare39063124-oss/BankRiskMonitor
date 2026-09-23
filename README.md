# BankRiskMonitor

银行管户企业风险监控工具（Python + PySide6 桌面应用）。

面向银行贷后管理场景：对管户企业的**舆情、工商变更、涉诉、被执行/查封**四类公开信息进行自动化采集，支持**股权上下穿透 1-2 层**发现关联企业风险，并通过 **AI Agent（OpenRouter 免费模型）** 对每条风险事件做情绪/严重度/相关性研判与授信影响分析；AI 不可用时自动降级为**本地规则研判引擎**，保证监控不中断。

> 仅采集公开渠道信息用于银行内部贷后管理，不向客户发送任何信息。详见文末免责声明。

## 功能列表

- **管户企业管理**：添加/删除监测对象，集中展示风险概览与地域地图分布（ECharts 中国地图，按严重度/信息类型着色）。
- **四类风险信息采集**（声明式数据源，可增删/启停/排序，无需改代码）：
  - 舆情：百度新闻、RSSHub、澎湃热榜等
  - 工商变更：爱企查、国家企业信用信息公示系统（gsxt）、商业 API
  - 涉诉：裁判文书网
  - 被执行/查封：全国法院执行信息公开网（zxgk）
- **股权穿透**：向上股东、向下对外投资各穿透 1-2 层（`config.yaml` 可调），把关联企业风险纳入监测。
- **AI Agent 研判（5 角色）**：事件研判（情绪/严重度/相关性/一句话结论）、批量研判、授信影响分析、监测报告文本生成、穿透关系分析。
- **免费模型 fallback**：`model_policy.yaml` 声明 OpenRouter 免费模型（cost==0）的尝试顺序，禁止元路由（如 `openrouter/auto`）。
- **自动降级与补跑**：OpenRouter 无响应/连续失败达阈值时自动切换本地规则引擎（词库打分 + 来源可信度加权），降级结果统一标注 `pending_review` 并加降级前缀；任务在队列中积累，AI 恢复后自动补跑。
- **监测报告导出**：一键生成行内格式 Word 报告（黑体标题、宋体正文、1.5 倍行距、自动章节编号）。
- **限流与代理**：每主机每秒请求数限制、自定义 User-Agent、可配 HTTP 代理。
- **免安装便携**：打包后单个 exe，数据（SQLite `data/monitor.db`）存放在 exe 同目录，绿色免安装。

## 使用说明

### 开发运行

```bash
pip install -r requirements.txt
python main.py
```

要求 Python 3.11+。开发模式下配置文件与 `data/` 目录均位于项目根目录。

### 打包

```bat
build_exe.bat
```

产物为 `dist\BankRiskMonitor.exe`（onefile 模式）。如 onefile 因 QtWebEngine 报错，可编辑脚本将 `--onefile` 改为 `--onedir`。

### 打包运行（免安装交付）

将以下文件放在**同一目录**：

```
BankRiskMonitor.exe
config.yaml          # 可选，缺省用内置默认
model_policy.yaml    # 可选
.env                 # 可选，使用 AI 研判时需要
```

双击 `BankRiskMonitor.exe` 即可运行，数据自动生成在 exe 同目录的 `data\` 下（含 `monitor.db`）。

## 配置说明

### config.yaml

- `app`：应用名称、自动刷新周期（分钟）、HTTP 代理。
- `penetration`：股权穿透开关与上下穿透层数（1-2）。
- `sources`：**声明式数据源登记**。每类信息（news / biz_change / lawsuit / enforcement）是一个有序 Provider 链，排在前面的优先；`enabled` 控制启停；`params` 中的 key 留空表示该源未启用（自动跳过）。新增数据源只需登记一行，无需改代码。
- `network`：超时、限流、User-Agent。

### model_policy.yaml

- `policy`：仅允许免费模型（`require_cost_zero: true`），封禁 `openrouter/auto` 等元路由。
- `fallback_models`：OpenRouter 免费模型 fallback 列表，按序尝试。
- `request`：超时、重试、退避与降级阈值（连续失败 N 次进入降级模式）。

### .env

存放 OpenRouter API Key（用于 AI 研判）：

```
OPENROUTER_API_KEY=你的key
```

`.env` 已被 `.gitignore` 排除，不会进入版本库。

## 国内网络说明

- **OpenRouter 直连可能无响应**：此时应用自动降级为本地自动化规则脚本研判（结果标注 `engine=rule`、`pending_review=1`，文本带降级前缀），采集与监控流程不受影响；AI 恢复后队列自动补跑。
- 可在 `config.yaml` 的 `app.proxy` 配置 HTTP 代理（如 `http://127.0.0.1:7890`）改善 OpenRouter 连通性。
- 部分公开数据源（gsxt、裁判文书网等）反爬较强，采集失败会记录日志并跳过，不影响其他数据源。

## 免责声明

1. 本工具仅采集**公开渠道**信息（公开新闻、政府公示、法院公开信息等），仅用于**银行内部贷后管理与风险监测**。
2. 本工具**不向客户发送任何信息**，不进行任何形式的对外触达。
3. 采集行为应遵守目标网站的服务条款及相关法律法规，使用方需自行控制采集频率与范围。
4. AI 研判结果仅供参考，不构成授信决策依据；降级模式下的规则研判结果均需人工复核。
5. 请妥善保管 `.env` 中的 API Key，不要将其提交到代码仓库或泄露给他人。

## License

[MIT](LICENSE)
