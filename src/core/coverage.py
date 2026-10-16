"""必要信息域覆盖度检查（贷后必备信息不得静默缺失）。

背景：涉诉、被执行属贷后检查的**必备信息**。若某必备信息域在扫描时**没有任何
可靠数据源**（只有反爬站点这类"尽力而为"源，或干脆一个都没启用），界面必须在
扫描启动时**明确提示缺什么、怎么补**，绝不静默。

覆盖度分级（对某个 category）：
  - ``ok``      ：至少一个「可靠」源（tier 1 确定可用，或 tier 2 条件可用且已配置凭据）
  - ``weak``    ：无可靠源，但仍有「尽力而为」源（tier 3 反爬站点）——可能拉不到数据
  - ``missing`` ：无任何已启用源

配置（config.yaml）：
    required_coverage:
      - category: lawsuit
        label: 涉诉
        hint: "补充办法..."
未配置时使用 ``DEFAULT_REQUIRED_COVERAGE`` 兜底。
"""
from typing import Dict, List, Optional

# 凭据类参数名（requires_key/paid 的源需其中至少一项有值才算"已配置"）
_KEY_PARAMS = ("api_key", "cookie", "token", "secret_key")

# 兜底：贷后必备信息域
DEFAULT_REQUIRED_COVERAGE: List[Dict[str, str]] = [
    {
        "category": "lawsuit",
        "label": "涉诉",
        "hint": "裁判文书网反爬严格，页面采集不稳定；"
                "建议用工商连接器（企查查/天眼查/启信慧眼/水滴信用）代查后，"
                "按 docs/CONNECTOR-DATA-REQUEST.md 导入标准格式文件。",
    },
    {
        "category": "enforcement",
        "label": "被执行/失信",
        "hint": "执行信息公开网有验证码；"
                "建议用工商连接器代查后，按 docs/CONNECTOR-DATA-REQUEST.md 导入标准格式文件。",
    },
]

LEVEL_OK = "ok"
LEVEL_WEAK = "weak"
LEVEL_MISSING = "missing"

_LEVEL_TEXT = {
    LEVEL_OK: "可用",
    LEVEL_WEAK: "仅有尽力而为源（反爬站点，可能拉不到数据）",
    LEVEL_MISSING: "无可用数据源",
}


def _resolve_tier(spec: dict) -> int:
    """复用 providers.base.resolve_tier；导入失败时退化为本地推断（避免硬依赖）。"""
    try:
        from ..providers.base import resolve_tier  # 延迟导入，避免 GUI 启动期循环依赖
        return resolve_tier(spec)
    except Exception:  # noqa: BLE001
        spec = spec or {}
        try:
            t = int(spec.get("tier"))
            if t in (1, 2, 3):
                return t
        except (TypeError, ValueError):
            pass
        if spec.get("paid") or spec.get("requires_key"):
            return 2
        params = spec.get("params") or {}
        if "api_key" in params and not params.get("api_key"):
            return 2
        return 1


def _has_credentials(spec: dict) -> bool:
    """条件可用（tier 2）的源，是否已配置至少一项凭据。"""
    params = spec.get("params") or {}
    return any(str(params.get(k) or "").strip() for k in _KEY_PARAMS)


def _is_reliable(spec: dict) -> bool:
    """是否「可靠」源：tier 1，或 tier 2 且已配置凭据。tier 3（反爬）不算可靠。"""
    tier = _resolve_tier(spec)
    if tier == 1:
        return True
    if tier == 2:
        return _has_credentials(spec)
    return False


def category_coverage(cfg: Optional[dict], category: str) -> Dict:
    """单个信息域的覆盖度明细。"""
    cfg = cfg or {}
    specs = list((cfg.get("sources", {}) or {}).get(category, []) or [])
    enabled = [s for s in specs if s.get("enabled")]
    reliable = [s for s in enabled if _is_reliable(s)]
    best_effort = [s for s in enabled if not _is_reliable(s)]
    if reliable:
        level = LEVEL_OK
    elif best_effort:
        level = LEVEL_WEAK
    else:
        level = LEVEL_MISSING
    return {
        "category": category,
        "level": level,
        "level_text": _LEVEL_TEXT[level],
        "total": len(specs),
        "enabled": [s.get("name", "?") for s in enabled],
        "reliable": [s.get("name", "?") for s in reliable],
        "best_effort": [s.get("name", "?") for s in best_effort],
        "disabled": [s.get("name", "?") for s in specs if not s.get("enabled")],
    }


def required_coverage(cfg: Optional[dict] = None) -> List[Dict[str, str]]:
    """读取 config 的 required_coverage，缺失时用默认兜底。"""
    cfg = cfg or {}
    items = cfg.get("required_coverage")
    if not isinstance(items, list) or not items:
        return [dict(x) for x in DEFAULT_REQUIRED_COVERAGE]
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("category"):
            continue
        out.append({
            "category": str(it["category"]),
            "label": str(it.get("label") or it["category"]),
            "hint": str(it.get("hint") or ""),
        })
    return out or [dict(x) for x in DEFAULT_REQUIRED_COVERAGE]


def check_required_coverage(cfg: Optional[dict] = None) -> List[Dict]:
    """返回**需要提示**的必备信息域（level 为 weak/missing）。

    每项含：category/label/hint/level/level_text/enabled/reliable/best_effort/disabled。
    全可靠时返回空列表。
    """
    cfg = cfg or {}
    problems: List[Dict] = []
    for item in required_coverage(cfg):
        cov = category_coverage(cfg, item["category"])
        if cov["level"] == LEVEL_OK:
            continue
        cov.update({"label": item["label"], "hint": item["hint"]})
        problems.append(cov)
    return problems


def coverage_message(problems: List[Dict]) -> str:
    """把问题清单渲染成给用户看的提示正文（用于扫描启动弹窗/状态栏）。"""
    if not problems:
        return ""
    lines = ["以下贷后必备信息域缺少可用数据源，本次扫描这些信息可能无法采集：", ""]
    for p in problems:
        lines.append(f"· {p['label']}（{p['category']}）：{p['level_text']}")
        if p.get("best_effort"):
            lines.append(f"    尽力而为源：{'、'.join(p['best_effort'])}")
        if p.get("disabled"):
            lines.append(f"    已停用源：{'、'.join(p['disabled'])}")
        if p.get("hint"):
            lines.append(f"    补充办法：{p['hint']}")
        lines.append("")
    lines.append("提示：用工商连接器代查企业档案 → 生成标准格式文件 → 用 tools/connector_ingest.py 导入，即可补齐上述信息。")
    return "\n".join(lines)
