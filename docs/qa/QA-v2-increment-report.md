# QA 独立验收报告 · BankRiskMonitor v2 增量（R1–R5）

- 验收人：严过关（QA） · 日期：2026-10-16 · 解释器：`D:\hermes\hermes-agent\venv\Scripts\python.exe`（Py3.11.9 / PySide6 6.11.2）
- 基线：`git b5532dd..f9e05e3`（工作区干净）
- 方法：**证伪式独立验收**（自造数据 + 自定断言），不复跑工程师的用例
- 最终结论：**IS_PASS: NO**（路由 → **Engineer Alex**，见 §④）

---

## ① 新增/修改的测试文件清单（均为 QA 新增，不改产品代码）

| 文件 | 覆盖 |
|---|---|
| `qa_verify/conftest.py` | 路径/offscreen 统一夹具 |
| `qa_verify/test_qa_r1_baseline.py` | A. R1 判定矩阵 / 边界 / 脏日期 / 11 维 / 纯函数 / **历史基线取最近状态** |
| `qa_verify/test_qa_r2_sources.py` | B. 数据源四类故障注入 / config 策略 / connector_ingest 幂等与硬约束 |
| `qa_verify/test_qa_t01_map.py` | C. 原生地图渲染 PNG + 像素占比 / 四级下钻 / 降级文案 / 光点点击信号 |
| `qa_verify/test_qa_r3r4_gui.py` | D. R3 详情弹窗双击 / R4 联动含关联 / 预警仅 change / 空态 |
| `qa_verify/test_qa_r5_shutdown.py` | E. 单实例（进程内+子进程）/ closeEvent 回收 |
| `qa_verify/inspect_exe.py` | F. 独立解析 exe 内嵌归档 TOC |
| `docs/qa/map_national_qa.png`、`docs/qa/map_pixel_stats.txt` | C. 地图渲染证据 |

> 未修改、未删除任何既有测试（`git log --diff-filter=D -- tests/` 为空）。

---

## ② 全量测试结果

- **产品既有用例**：`QT_QPA_PLATFORM=offscreen pytest tests/ -q` → **222 passed, 10 subtests passed**（20.62s），与工程师自报一致（独立复跑）。
- **QA 独立验收用例**：`pytest qa_verify/ -q` → **45 tests：40 passed / 5 failed，49 subtests passed**。
- 失败 5 条即下 §④ 的缺陷证据（4 条指向同一 R1 缺陷，1 条为低危配置一致性）。

---

## ③ A–F 独立证据

### A. R1 基线判定（可复现证据）
- 判定矩阵 **N1/N2/N3/N4** 全组合均符合 PRD §3.6（`test_qa_r1_baseline.py::MatrixQA` 全绿）。
- **同日边界**：`change_date==loan_date` → `change` ✅；`loan_date-1天` → `baseline` ✅。
- **脏日期**：`""` / `None` / `"   "` / `"2026/09/29"` / `"abc"` / `"20260929"` → 全部 `undetermined(no_date)` 且不崩溃 ✅；`"2026-09-29 10:00:00"` / `"  2026-09-29  "` 正确归一 ✅。
  - ⚠️ `"2026-13-45"`（月/日非法）**未判 undetermined 而判 change** —— 见 §④ BUG-2。
- **11 维**：变更/未变更各 1 例全部符合（`DimensionsQA::test_all_11_dimensions_change_and_no_change`）✅。
- **涉诉/被执行按"较基线新增"**：基线存量 50 起 + 放款后 1 起新案 → `change`（增量 50→51，不因总量大误判）✅；基线已"被执行人"→ 再报被执行 = `baseline`（存量非问题）✅。
- **`classify()` 纯函数**：连续 6 次调用结果逐字段一致 ✅；将 `db.get_baseline/list_events/get_enterprise/upsert_baseline/list_baselines/delete_baseline` 全部 patch 为"一调用即抛"后 `classify` 仍正常返回、且**零调用** ✅。
- ❌ **历史基线取"最早"而非"最近"**：`HistoricBaselineLatestStateTest` — 放款前两次法代变更（张三→李四→王五），基线应=**王五**，实测=**李四**；并由此产生 **误报** 与 **漏报**。见 §④ BUG-1。

### B. R2 数据源
- 对 `credit_china` 注入 **超时 / HTTP 500 / 反爬 HTML / 非 JSON** 四故障：`run_with_fallback` 均返回 `([], err含源名)`，**无未捕获异常**，耗时 <5s（实测毫秒级）✅。
- 对 `gsxt` 注入 **超时 / HTTP 500 / 反爬短页** 三故障：同上 ✅。
- 刷新链整体（源全挂 → `run_sync`）不抛异常、不打断 ✅。
- `config.yaml`：**全部 `paid:true` 源默认 `enabled:false`** ✅；`jisuapi` 默认关闭且标为付费、未被当免费工商源 ✅；P0 免费源（gsxt/credit_china/wenshu/zxgk/aiqicha）均启用 ✅。
  - ⚠️ `istero_pengpai` 为 `requires_key:true` 但 `enabled:true` —— 见 §④ DEV-3。
- `connector_ingest.py`：临时库跑两遍 → `inserted` 5 → **0** ✅；**库内无任何 `occurred_at == generated_at(2026-10-16)`** ✅；未授权(401/403)不抛异常、`inserted=0` ✅；未知连接器不阻断 ✅。

### C. T01 原生地图（"真的画出来了"）
- offscreen 构造 `MapView`，`set_data` 灌入 high/medium/low/none 四档，`resize(1200,800)` → `grab()`：
  - **PNG：`docs/qa/map_national_qa.png`**（159 KB）
  - **非背景像素占比 = 5.52%**（采样 106800 点，非背景 5900 点；背景 `#0D1420`）→ 远高于"非空白"阈值 2%。
  - 目视确认：中国省界霓虹描边 + 4 个分档配色光点（红=辽宁、橙=浙/苏、黄=粤、绿=川）+ 右下角图例。
- **四级下钻**：全国→辽宁省→沈阳市→区县，逐级 `level` 推进且均有边界数据，不崩溃 ✅。
- **`has_children=False`**：浙江→杭州（无区县文件）→ `drill()` 返回 False、层级不变、`_msg` 显示降级文案"该区域无下级边界数据" ✅。
- **光点点击**：合成 `mousePress`+`mouseRelease` 命中光点 → emit `spot_clicked(企业名)` ✅。

### D. R3/R4 交互
- 双击事件流 / 双击预警 **均打开 `EventDetailDialog`**（patch 消息循环后捕获），字段 = 元信息 9 + 标题 + 摘要 + AI 研判 + 原文链接 = **13 ≥ 12** ✅。
- **预警区只含 change**：灌入混合 bucket（change×2 / baseline×1 / undetermined×1，其中基线/无法判定均为 high）→ 预警恰 2 条，放款前高危与日期未知项**均未进入** ✅。
- 点击左侧"鑫泰地产"→ 右侧事件流即时筛选，**含穿透关联主体**（主+关联均可见、无其他企业混入），关联行带"关联"标签，预警同步过滤 ✅。
- 空态文案：事件流"当前筛选无事件…"、企业清单"暂无监控企业…"、预警"报告期内无放款后新增风险" 均存在 ✅。

### E. R5 进程 / 单实例（重新实测）
- **真实 exe**（`dist_qa_verify/BankRiskMonitor.exe`）`tools/verify_shutdown.py --mode real --runs 2`：
  - 启动两实例 → **窗口数恒为 1**，第二实例退出码 0、秒退 ✅
  - `WM_CLOSE` → 进程退出耗时 **0.50s**（≤3s）✅
  - `tasklist` 独立复核：**无 `BankRiskMonitor.exe` / `QtWebEngineProcess.exe` 残留**（`False/False`）✅
- 离线可复现替代证据：`SingleInstanceGuard` 主/次/激活/释放全部通过；`closeEvent` 3s 内回收在跑线程并停 `_tick_timer`；两个 `python main.py`(offscreen) 子进程 → 第二实例 <5s 退出、退出码 0 ✅。

### F. 打包与回归
- 独立执行 `pyinstaller --noconfirm BankRiskMonitor.spec`（dist→`dist_qa_verify`）→ 成功。
- `tools/verify_package.py --dist dist_qa_verify` → **[PASS]**，**总体积 63.46 MB ≤ 80MB** ✅。
- 独立解析 exe 内嵌归档 TOC：
  - **含 `Qt6Network.dll` = True** ✅（单实例依赖）
  - `Qt6WebEngineCore.dll / QtWebEngineProcess.exe / icudtl.dat / resources.pak / Qt6Qml.dll / opengl32sw.dll` 均为 **False** ✅
  - translations 3 项（均 zh_CN）、qml 0 项 ✅
- `git log --diff-filter=D -- tests/` 空 → **未删除任何既有用例** ✅。

---

## ④ 问题清单与智能路由判定

| # | 严重度 | 归因 | 复现 / 证据 |
|---|---|---|---|
| **BUG-1** | **中·高** | **源码 Bug → Engineer** | 见下 |
| **BUG-2** | 低 | **源码 Bug → Engineer** | 见下 |
| **DEV-3** | 低·可选 | **Engineer（低危/一致性，请 PM 确认）** | 见下 |

### BUG-1（R1 核心正确性）— 历史来源建基线取"最早状态"而非"放款前最近状态"
- **文件:行号**：`src/core/baseline_compare.py` → `_apply_history()`（L467–L504，尤其 `pre.sort(...)` L471 与 `_merge_dim` L403–L410 的"首个非空即留、后续不覆盖"）。
- **复现步骤**：
  1. 企业 id=1，`loan_date="2026-09-29"`；
  2. 插入放款前事件两条：`(2026-08-01) 法定代表人由张三变更为李四`、`(2026-09-10) 法定代表人由李四变更为王五`；
  3. `s = build_baseline(ent)`；
  4. `classify(ev("法定代表人由王五变更为李四", 2026-10-05), ent, s)`。
- **期望**：`s.legal_person == "王五"`（放款时点状态）；第 4 步相对真实基线（王五）结果应为 `change`（真·变回旧值）。
- **实际**：`s.legal_person == "李四"`（取了**最早**一次变更后的值）；第 4 步实测 `baseline`（**漏报**）。
- **对偶误报**：`classify(ev("法定代表人由赵六变更为王五"), ...)` 结果为基线值（王五）应 `baseline`，实测 `change`（**误报**）。
- **失败断言**：`qa_verify/test_qa_r1_baseline.py::HistoricBaselineLatestStateTest` 三条用例。
- **根因**：`_apply_history` 对 `STATE_DIMS`（法定代表人/注册资本/股东/经营范围/经营状态）按日期**升序**迭代且 `_merge_dim` 只写首次非空 → 取到**最早**变更值；正确应为**放款前最近一次**变更后的值（"放款时点状态"）。
- **影响**：R1 首要基线来源（历史事件 / 带日期变更记录）一旦同一维度有 ≥2 次放款前变更，基线即错，导致放款后对比**误报/漏报**。虽当前真实库因 `tianyancha_mcp` 行 `occurred_at` 已清空而暂未触发，属**潜伏缺陷**。
- **建议**：`_apply_history` 对 `STATE_DIMS` 取**放款前最近一次**变更（或将 `pre` 倒序后再逐维首写）；`_apply_auto_source` 的 `changes` 亦建议按 `change_date` 排序后取最近。

### BUG-2（低）— `parse_date` 接受语义非法日期
- **文件:行号**：`src/core/baseline_compare.py` → `parse_date()` L157–L162。
- **复现**：`classify(ev(occurred_at="2026-13-45"), ent, snap(legal_person="张三"))`。
- **期望**：`undetermined`（PRD §3.5：任一日期非法 → undetermined；`parse_date` 自身 docstring 亦称"非法→''"）。
- **实际**：`parse_date("2026-13-45")` 返回 `"2026-13-45"`（仅校验形状 `\d{4}-\d{2}-\d{2}`，不校验月/日范围）→ 判为 `change`。
- **失败断言**：`MatrixQA::test_semantically_invalid_date_should_be_undetermined`。
- **影响**：脏数据（如上游写入非法月/日）会被当合法日期参与对比，可能误判。触发概率低。
- **建议**：`parse_date` 增加月/日范围校验（如 `datetime.strptime`）或将非 `1-12`/`1-31` 视为非法。

### DEV-3（低·可选）— `istero_pengpai` 需 Key 却默认启用
- **文件:行号**：`config.yaml` L39–L46（`requires_key: true` 且 `enabled: true`）。
- **期望**（团队口径）：`requires_key:true` 的源默认 `enabled:false`。
- **实际**：唯一违规项 `istero_pengpai`。
- **评估**：属**免费**但需 token 的第三方舆情源；因 `token:""`，provider 抛异常由 `run_with_fallback` 自动跳过 → **功能上不崩不卡，无实际危害**；PRD §4 仅强令"付费源关闭"，未禁止免费需 Key 源。故判**低危·一致性提示**，建议二选一：置 `enabled:false`，或在 config 注释中显式声明"免费需 Key 源可默认启用、缺 Key 自跳过"以消除口径歧义。

---

## ⑤ 最终判定

- **IS_PASS: NO**
- **路由**：**Engineer（Alex）** 修复 **BUG-1（中高）**、**BUG-2（低）**；**DEV-3** 低危、待 PM/Engineer 定夺。
- **其余 6 项（B/C/D/E/F 全部 + A 的矩阵/边界/脏日期/11 维/纯函数/新增口径）均独立通过**，无回归。
- 说明：本轮 QA 为 **Round 1**（已耗尽一次修复回归额度中的发现轮）；BUG-1 修复后需回归 `qa_verify/test_qa_r1_baseline.py`（重点 `HistoricBaselineLatestStateTest`）。
