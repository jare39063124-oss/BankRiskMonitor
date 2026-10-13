# Spec: BankRiskMonitor 深色监测大屏改版

> 模块: map-dark-theme（单模块规格，含主题/地图/弹层/演示数据四块能力）
> 日期: 2026-10-13 ｜ 状态: 待确认

## 1. Objective

把现有"银行蓝浅色"工作台改造成对标全球情报监测大屏（如 HackNet/威胁情报地图）的**黑色科技风监控界面**：

- 地图纯黑底、行政区划边界**荧光绿**（#00FF9C）描边，带辉光效果
- 舆情光点按**企业注册城市**的真实经纬度落点（不再是省中心堆叠）
- **点击省份下钻**到市级精细边界，返回按钮回到全国
- **点击光点弹出舆情详情卡**：新闻标题、来源、时间、风险等级、AI 研判、摘要
- 全软件（工具栏/面板/弹窗/状态栏）统一深色主题

**控件可辨识性与点击反馈（硬性要求）**
- 所有可点击控件（按钮/下拉/页签/列表项/工具栏动作）在深色底上必须有明确视觉区分：
  有可见边框或填充底色，与背景对比度足够（正文/控件文字对比度 ≥ 4.5:1）
- 三态样式缺一不可（QSS 强制覆盖，不允许控件裸奔在背景上）：
  - **常态**：深色底 `#161C26` + 可辨识边框（普通态 `#2A3441`，主操作荧光绿 `#00FF9C` 描边 + 绿字）
  - **hover**：边框变荧光绿、文字提亮、外辉光 `box-shadow` 微光效果
  - **pressed（点击反馈）**：底色/边框颜色立即反转（绿底深字或亮绿填充），视觉上"按下去"
- 页面级反馈：每次点击按钮后必须有可感知响应——状态栏消息提示（沿用现有机制）或对话框/进度提示，
  禁止"点了没反应"的按钮
- 地图点击反馈：悬停省份高亮（荧光绿描边+区域微亮），点击省份下钻时边界短暂高亮闪烁；
  点击光点弹层带淡入动画

**用户故事**
1. 银行贷后经理打开系统，看到黑色大屏地图，光点在城市位置脉动，高危点红光、中危橙光
2. 双击/单击某省 → 地图放大显示该省各市边界（荧光绿描边），光点自动落到对应市
3. 点击某光点 → 弹出霓虹风格详情卡，能看到该企业最新舆情内容和 AI 研判
4. 整体观感：酷炫、专业、有"情报中心"氛围

## 2. Tech Stack（不变）

- Python + PySide6（QWebEngineView 内嵌地图，QWebChannel 桥接）
- ECharts 5（本地 `echarts.min.js`，零网络访问）
- 阿里云 DataV GeoJSON（**开发期下载打包**，运行时离线）：`geo.datav.aliyun.com/areas_v3/bound/{adcode}_full.json`
- SQLite（data/monitor.db）

## 3. Commands

```bash
# 运行（开发模式）
cd E:\软件工程项目\BankRiskMonitor && python main.py

# 下载/更新地图数据（开发期执行一次，产物提交进 assets）
python tools\fetch_geo.py          # 下载 34 省级(市级边界)+城市中心点表

# 生成演示数据
python tools\seed_demo.py          # 12 家企业 + ~60 条事件（含各风险等级）

# 测试（现有 85 个单测必须全绿）
python -m pytest tests\ -x -q

# 打包
build_exe.bat
```

## 4. Project Structure（改动范围）

```
src/
├── assets/
│   ├── map.html            ★ 重写：黑底荧光绿主题、下钻、光点详情弹层
│   ├── geo/
│   │   ├── city_centers.json   ★ 新增 ~350 城市 {名称,adcode,经纬度,父省}
│   │   └── provinces/          ★ 新增 34 个 {adcode}_full.json（市级边界）
│   ├── china.json            保留（全国省级轮廓）
│   └── echarts.min.js        保留
├── gui/
│   ├── theme.py            ★ 重写：深色 tokens + QSS（黑底/荧光绿 accent/霓虹高危色）
│   ├── map_view.py         ★ 扩展：下钻信号、详情数据推送、adcode 注册
│   ├── main_window.py      ○ 小改：数据聚合改为城市粒度、详情数据组装、双击下钻联动
│   ├── panels.py           ○ 微调：面板标题栏霓虹样式（走 QSS，基本自动生效）
│   └── dialogs.py / report_page.py / workers.py   ○ 基本不动
├── core/
│   └── db.py               ○ 不动（city 字段已存在）
tools/
├── fetch_geo.py            ★ 新增：地图数据下载器（含校验/断点续传）
└── seed_demo.py            ★ 新增：演示数据生成器
tests/
└── test_map_geo.py         ★ 新增：城市归一化/坐标回退/聚合逻辑单测
```

## 5. Code Style（沿用现有约定 + 新增示例）

- 中文 docstring 首行概述；`_` 前缀私有；dataclass 契约字段不增删
- map.html 内 JS 用 ES5（QWebEngine 兼容），全局状态集中管理

```python
# 城市名归一化：地图定位的唯一入口（src/core/geo.py 新增）
def norm_city(name: str) -> str:
    """'杭州市' -> '杭州'；'市'后缀剥离，空值返回 ''。"""

def city_center(province: str, city: str) -> tuple[float, float] | None:
    """查 city_centers.json；city 查不到回退省中心，再查不到返回 None。"""
```

```javascript
// map.html：光点数据契约（renderSpots 入参，向后兼容）
// [{ name, province, city, event_count, severity, categories,
//    events: [{title, source, occurred_at, severity, summary, ai_verdict}] }]
```

## 6. Testing Strategy

- 框架：pytest（沿用 tests/ 现有风格，GUI 逻辑离屏测试）
- `tests/test_map_geo.py`：
  - `norm_city`：直辖市/县级市/空值/带空格输入
  - `city_center`：命中/回退省中心/双回退 None
  - 聚合函数：同市多企业分组、severity 取最高
- 人工验收清单（跑 `python main.py`）：
  - [ ] 全界面黑底，地图边界荧光绿
  - [ ] 光点落在北京/杭州等真实城市位置，不堆在省中心
  - [ ] 点击省份下钻出市级边界，返回键回全国
  - [ ] 点击光点弹详情卡，内容与该企业事件一致
  - [ ] 滚轮缩放、拖拽平移正常；断网可用
  - [ ] 每个按钮常态/hover/pressed 三态均有肉眼可辨的变化；点击后状态栏或弹层有响应

## 7. Boundaries

- **Always**: 运行时零网络请求（拦截器保留）；提交前 `pytest tests\ -x -q` 全绿；地图数据提交进 assets 保证离线打包
- **Ask first**: 修改 core/models.py 契约字段；更换 GeoJSON 数据源；增删第三方依赖
- **Never**: 页面运行时加载在线瓦片/在线 ECharts；删除现有 85 个单测；把 API key 写进代码

## 8. Success Criteria（可测）

1. `pytest tests\ -x -q` 全绿（85 旧 + 新增 geo 测试）
2. 视觉：`python main.py` 截图确认黑底 + 荧光绿边界 + 光点在城市位置
3. 交互：省下钻、光点详情弹层、滚轮缩放三项人工验收通过
4. 控件反馈：所有按钮 hover/pressed 有可见状态变化，点击后有页面级响应（人工验收）
5. 离线：拔网线（或断网环境）启动，全部功能可用
6. 打包：`build_exe.bat` 成功，产物含 geo 资源，启动正常

## 9. Open Questions

- 无（四个关键决策已确认：全软件深色 / 城市级定位 / 两级下钻 / 提供演示数据）

## 能力映射（Phase 0）

| 模块 id | 职责 | 依赖 |
|---|---|---|
| theme-dark | 全局深色 tokens + QSS | — |
| map-geo | 城市坐标表、下钻渲染、黑底荧光绿地图 | theme-dark |
| popup-news | 光点点击舆情详情弹层 | map-geo |
| demo-data | 演示数据生成器（仅开发/验收用） | — |

Build order: theme-dark → map-geo → popup-news（demo-data 并行）
