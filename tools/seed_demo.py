"""演示数据生成器（开发/验收用，不影响正式功能）。

生成 12 家监控企业（覆盖多省市）与约 60 条事件（含各风险等级/信息域），
用于视觉验收：光点城市定位、下钻、详情弹层。

运行：python tools/seed_demo.py        （幂等：重复运行不产生重复数据）
"""
import os
import random
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.core import db  # noqa: E402
from src.core.models import Event  # noqa: E402

random.seed(42)

ENTERPRISES = [
    # (名称, 省份, 城市, 授信备注)
    ("恒信实业集团有限公司", "浙江省", "杭州市", "流动资金贷款 8000 万"),
    ("中科恒泰新能源科技有限公司", "江苏省", "苏州市", "项目贷款 1.2 亿"),
    ("华辰置业发展有限公司", "广东省", "广州市", "开发贷 2.5 亿"),
    ("金桥港口物流股份有限公司", "上海市", "上海市", "银团贷款 3 亿"),
    ("天晟精密制造有限公司", "浙江省", "宁波市", "技改贷款 5000 万"),
    ("蜀都食品集团有限公司", "四川省", "成都市", "供应链融资 3000 万"),
    ("蓝湾海洋生物科技有限公司", "山东省", "青岛市", "研发贷 2000 万"),
    ("北疆重工装备股份有限公司", "辽宁省", "沈阳市", "设备按揭 6000 万"),
    ("楚天医药化工有限公司", "湖北省", "武汉市", "并购贷款 1.5 亿"),
    ("闽东南电子信息技术有限公司", "福建省", "厦门市", "科技贷 1500 万"),
    ("中原农业发展集团有限公司", "河南省", "郑州市", "惠农贷 4500 万"),
    ("云上大数据服务股份有限公司", "贵州省", "贵阳市", "数据中心贷 9000 万"),
]

NEWS_TITLES = [
    ("{ent}被曝拖欠供应商货款超千万", "news", "high", "negative",
     "多家供应商向媒体反映货款逾期超 6 个月，涉及金额约 1200 万元"),
    ("{ent}中标某地智慧城市项目", "news", "low", "positive",
     "中标金额 3800 万元，对下半年现金流形成支撑"),
    ("{ent}高管接受监管部门约谈", "news", "medium", "negative",
     "媒体报道公司财务负责人被地方金融监管部门约谈，原因未披露"),
    ("{ent}被列入经营异常名录", "biz_change", "medium", "negative",
     "因未按期公示年度报告被市场监管部门列入经营异常名录"),
    ("{ent}法定代表人变更", "biz_change", "low", "neutral",
     "工商信息显示法定代表人由王某变更为李某，需关注治理层稳定性"),
    ("{ent}注册资本减资 20%", "biz_change", "medium", "negative",
     "注册资本由 2.5 亿元减至 2 亿元，可能反映股东资金压力"),
    ("{ent}新增买卖合同纠纷开庭公告", "lawsuit", "medium", "negative",
     "原告某建材公司，涉案金额约 800 万元，定于下月开庭"),
    ("{ent}因劳动争议被起诉", "lawsuit", "low", "neutral",
     "前员工主张违法解除劳动合同，涉案金额较小"),
    ("{ent}新增被执行人记录", "enforcement", "high", "negative",
     "执行标的 2300 万元，执行法院为当地中级人民法院"),
    ("{ent}名下房产被查封", "enforcement", "high", "negative",
     "核心园区两处不动产被轮候查封，估值约 9000 万元"),
    ("{ent}员工在社交平台爆料欠薪", "social", "medium", "negative",
     "多条帖子反映工资延迟发放，热度持续上升，存在舆情发酵风险"),
    ("{ent}发布新品获行业好评", "social", "low", "positive",
     "行业协会转发，正面舆情为主，短期无风险信号"),
]

AI_VERDICTS = [
    "涉诉及被执行信息与授信主体直接相关，建议下调风险分类并加密监测频率。",
    "正面舆情为主，未发现与授信偿还能力直接相关的负面信号，维持现有监测策略。",
    "负面舆情涉及资金链，建议核实企业银行流水与纳税数据，评估第二还款来源。",
    "工商变更属中性事项，但减资行为需结合股东出资能力进一步核实。",
]


def main() -> int:
    db.init_db()
    ent_ids = {}
    for name, prov, city, note in ENTERPRISES:
        eid = db.upsert_enterprise(name=name, province=prov, city=city, credit_note=note)
        ent_ids[name] = eid

    today = datetime.now()
    inserted = 0
    for name, eid in ent_ids.items():
        n_ev = random.randint(4, 7)
        picked = random.sample(NEWS_TITLES, n_ev)
        for i, (tpl, cat, sev, senti, summary) in enumerate(picked):
            title = tpl.format(ent=name)
            occurred = (today - timedelta(days=random.randint(1, 60))).strftime("%Y-%m-%d")
            verdict = random.choice(AI_VERDICTS) if random.random() < 0.5 else ""
            ev = Event(
                enterprise_id=eid, category=cat, title=title,
                summary=summary,
                url="", source=random.choice(["baidu_news", "qcc_api", "weibo", "court_announce"]),
                occurred_at=occurred, severity=sev, sentiment=senti,
                relevance="high" if sev == "high" else "medium",
                ai_verdict=verdict, engine="ai" if verdict else "rule",
            )
            if db.insert_event(ev):
                inserted += 1

    print(f"企业 {len(ent_ids)} 家（新增/更新），本次新增事件 {inserted} 条。")
    print("打开 python main.py 即可查看地图效果。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
