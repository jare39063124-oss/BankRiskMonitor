"""配置加载：config.yaml + model_policy.yaml，提供全局单例访问。

PyInstaller onefile 打包后，exe 同目录可能缺失配置文件（双击 exe 场景）。
本模块提供 ensure_boot_files() 启动自举：自动补齐缺失的 config.yaml /
model_policy.yaml / .env（.env 只写空模板，绝不包含真实密钥）；同时内置
默认配置兜底，保证 config()/model_policy() 永不因文件缺失或损坏而抛异常。
"""
import os
from typing import Any, Dict, List

import yaml

from .paths import app_root

_config: Dict[str, Any] = {}
_policy: Dict[str, Any] = {}

# 内置默认全局配置：内容与项目根 config.yaml 保持一致（自举与兜底共用）
DEFAULT_CONFIG_YAML = """\
# BankRiskMonitor 全局配置（声明式数据源登记：可新增/启停/排序，无需改代码）
# 数据源可用性分级：tier=1 确定可用 / tier=2 条件可用(需 Key/付费) / tier=3 尽力而为(反爬)
app:
  name: BankRiskMonitor
  refresh_interval_minutes: 30   # 定时刷新周期（0=关闭自动刷新）
  lookback_years: 3              # 舆情追溯窗口（年）
  relevance_filter: strict       # 相关性过滤：strict/off
  proxy: ""                      # HTTP 代理，如 http://127.0.0.1:7890；留空不使用

penetration:
  enabled: true
  up_levels: 2      # 向上股东穿透层数（1-2）
  down_levels: 2    # 向下对外投资穿透层数（1-2）

# 贷后必备信息域：扫描启动时若某域没有「可靠」数据源（只有反爬站点这类尽力而为源，
# 或一个都没启用），界面会明确提示缺什么、怎么补，绝不静默。
required_coverage:
  - category: lawsuit
    label: 涉诉
    hint: "裁判文书网反爬严格、页面采集不稳定。建议用工商连接器（企查查/天眼查/启信慧眼/水滴信用）代查后，按 docs/CONNECTOR-DATA-REQUEST.md 生成标准格式文件并导入。"
  - category: enforcement
    label: 被执行/失信
    hint: "执行信息公开网有验证码。建议同上：连接器代查 → 标准格式文件 → 导入。"

# 数据源声明式登记：category -> 有序 Provider 链（排在前面的优先）
# enabled: true/false 控制启停；params 中的 key 留空表示该源未启用（自动跳过）
# 本轮策略：只用免费/官方公开源；付费/需 Key 源一律 enabled:false 仅留配置位。
sources:
  news:
    - name: baidu_news
      enabled: true
      tier: 3
      class: providers.news_baidu.BaiduNewsProvider
      params: {}
    - name: sogou_news
      enabled: false
      tier: 3
      class: providers.news_sogou.SogouNewsProvider
      params: {}
    - name: rss
      enabled: true
      tier: 3
      class: providers.news_rss.RssProvider
      params:
        feeds:
          - https://rsshub.app/pengpai/channels/25
    - name: istero_pengpai
      enabled: false
      tier: 2
      requires_key: true
      class: providers.news_istero.IsteroNewsProvider
      params:
        base_url: https://api.istero.com/service/pengpaixinwen/reshoubang
        token: ""
    - name: newsapi
      enabled: false
      tier: 2
      requires_key: true
      class: providers.newsapi_org.NewsApiProvider
      params:
        api_key: ""
  biz_change:
    - name: aiqicha
      enabled: true
      tier: 2
      class: providers.biz_aiqicha.AiqichaProvider
      params:
        cookie: ""
    - name: gsxt
      enabled: true
      tier: 3
      class: providers.biz_gsxt.GsxtProvider
      params: {}
    - name: apizero
      enabled: true
      tier: 2
      class: providers.biz_apizero.ApiZeroProvider
      params:
        api_key: ""
    - name: tianapi
      enabled: false
      tier: 2
      paid: true
      requires_key: true
      supports_mcp: true
      class: providers.biz_commercial.CommercialApiProvider
      params:
        base_url: ""
        api_key: ""
        vendor: tianapi
    - name: jisuapi
      enabled: false
      tier: 2
      paid: true
      requires_key: true
      class: providers.biz_commercial.CommercialApiProvider
      params:
        base_url: ""
        api_key: ""
        vendor: jisuapi
    - name: juhe_ent
      enabled: false
      tier: 2
      paid: true
      requires_key: true
      class: providers.biz_juhe.JuheEnterpriseProvider
      params:
        api_key: ""
    - name: commercial_api
      enabled: false
      tier: 2
      paid: true
      requires_key: true
      class: providers.biz_commercial.CommercialApiProvider
      params:
        base_url: ""     # 天眼查/企查查/白牛等商业 API 地址
        api_key: ""
        vendor: tianyancha
  lawsuit:
    - name: wenshu
      enabled: true
      tier: 3
      class: providers.court_wenshu.WenshuProvider
      params: {}
    - name: commercial_api
      enabled: false
      tier: 2
      paid: true
      requires_key: true
      class: providers.biz_commercial.CommercialApiProvider
      params:
        base_url: ""
        api_key: ""
        vendor: qichacha
  enforcement:
    - name: zxgk
      enabled: true
      tier: 3
      class: providers.court_zxgk.ZxgkProvider
      params: {}
    - name: credit_china
      enabled: true
      tier: 3
      class: providers.credit_china.CreditChinaProvider
      params:
        endpoint: ""
    - name: commercial_api
      enabled: false
      tier: 2
      paid: true
      requires_key: true
      class: providers.biz_commercial.CommercialApiProvider
      params:
        base_url: ""
        api_key: ""
        vendor: qichacha
  social:
    - name: weibo
      enabled: true
      tier: 3
      class: providers.social_weibo.WeiboProvider
      params:
        cookie: ""
    - name: wechat_sogou
      enabled: true
      tier: 3
      class: providers.social_wechat.SogouWeixinProvider
      params:
        cookie: ""
    - name: douyin
      enabled: true
      tier: 3
      class: providers.social_douyin.DouyinProvider
      params:
        cookie: ""
    - name: kuaishou
      enabled: true
      tier: 3
      class: providers.social_kuaishou.KuaishouProvider
      params:
        cookie: ""
    - name: xiaohongshu
      enabled: true
      tier: 3
      class: providers.social_xiaohongshu.XiaohongshuProvider
      params:
        cookie: ""

network:
  timeout_seconds: 15
  max_per_host_rps: 1        # 限流：每主机每秒最大请求数
  user_agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36

ui:
  map_mode: severity   # severity=按严重度着色 / category=按信息类型着色
"""

# 内置默认模型策略：内容与项目根 model_policy.yaml 保持一致
DEFAULT_MODEL_POLICY_YAML = """\
# 模型策略：仅允许免费模型（cost == 0），禁止 openrouter/auto 等元路由
policy:
  require_cost_zero: true
  banned_models:
    - openrouter/auto
    - openrouter/*

# OpenRouter 免费模型 fallback 列表（按序尝试）
fallback_models:
  - id: deepseek/deepseek-chat-v3.1:free
    cost: 0
  - id: qwen/qwen3-235b-a22b:free
    cost: 0
  - id: meta-llama/llama-3.3-70b-instruct:free
    cost: 0
  - id: google/gemma-3-27b-it:free
    cost: 0
  - id: mistralai/mistral-small-3.2-24b-instruct:free
    cost: 0

request:
  timeout_seconds: 60
  max_retries: 2
  backoff_base_seconds: 2
  failure_threshold: 3   # 连续失败次数达到阈值后进入降级模式
"""

# .env 空模板：只提供键名占位，绝不写入真实密钥
DEFAULT_ENV = "OPENROUTER_API_KEY=\n"


def _load(path: str, default_text: str = "") -> Dict:
    """读取 YAML 文件；缺失/损坏/无权限时回退到内置默认内容并尽力重建文件。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        # 兜底：回退解析内置默认配置，保证调用方永不抛 FileNotFoundError
        try:  # 尽力把默认内容写回磁盘供用户修改；失败不影响返回
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(default_text)
        except OSError:
            pass
        return yaml.safe_load(default_text) or {}


def ensure_boot_files(target_dir: str = "") -> List[str]:
    """启动自举：确保目标目录（默认 exe 同目录/项目根）存在三个引导文件。

    - config.yaml / model_policy.yaml：缺失时写入内置默认内容（UTF-8）
    - .env：缺失时写入空模板 DEFAULT_ENV；已存在则完全不动（不覆盖、不追加）
    返回本次生成的文件名列表；单个文件写入失败（如目录只读）时跳过该文件，
    不抛异常，保证启动流程不中断。
    """
    base = target_dir or app_root()
    generated: List[str] = []
    jobs = [
        ("config.yaml", DEFAULT_CONFIG_YAML),
        ("model_policy.yaml", DEFAULT_MODEL_POLICY_YAML),
        (".env", DEFAULT_ENV),
    ]
    for name, content in jobs:
        path = os.path.join(base, name)
        try:
            if os.path.exists(path):
                continue  # 已存在：完全不碰
            os.makedirs(base, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            generated.append(name)
        except OSError:
            continue  # 目录只读等异常：跳过该文件，不让启动崩溃
    return generated


def config() -> Dict[str, Any]:
    global _config
    if not _config:
        _config = _load(os.path.join(app_root(), "config.yaml"), DEFAULT_CONFIG_YAML)
    return _config

def model_policy() -> Dict[str, Any]:
    global _policy
    if not _policy:
        _policy = _load(os.path.join(app_root(), "model_policy.yaml"), DEFAULT_MODEL_POLICY_YAML)
    return _policy

def reload():
    global _config, _policy
    _config, _policy = {}, {}

def save_config():
    with open(os.path.join(app_root(), "config.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(_config, f, allow_unicode=True, sort_keys=False)
