# Plan: 深色监测大屏改版（SPEC-map-dark-upgrade.md 的实施计划）

## 实施顺序（依赖驱动）
1. tools/fetch_geo.py + 下载产物（assets/geo/）——无依赖，最先做（体积大、可并行验证）
2. src/core/geo.py ——依赖 1 的 city_centers.json
3. theme.py 深色重写——无依赖，可与 1/2 并行
4. map.html 重写——依赖 1（下钻 XHR 路径约定）
5. main_window.py / map_view.py 数据链路——依赖 2、4 的数据契约
6. seed_demo.py + test_map_geo.py + 全量测试——依赖以上全部

## 风险与对策
- 数据体积：34 省市级 GeoJSON 预计 15~30MB → 打包 spec 需包含 assets/geo（检查 BankRiskMonitor.spec）
- ECharts 大 JSON 注册卡顿：registerMap 用简化版（DataV full 已够用），下钻时加载动画兜底
- runJavaScript 大载荷：下钻不走路径（页面 XHR 本地文件），Qt 只传 adcode
- 对比度：荧光绿 #00FF9C 在黑底对比度 >12:1，达标；正文用 #E6EDF3
- 打包资源路径：paths.py asset_path 已兼容 _MEIPASS，geo 子目录沿用相对路径约定

## 验证检查点
- 每步后 pytest tests/ -x -q 保持绿
- 最终：python main.py 人工验收（黑底/荧光绿/落点/下钻/弹层/按钮三态/断网）
