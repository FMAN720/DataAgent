"""Disjoint synthetic calibration and holdout questions, committed before API calls."""

import json
from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "app/demo/documents"


def corpus():
    return [
        {**item, "content": (CORPUS_ROOT / item["file"]).read_text(encoding="utf-8")}
        for item in json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    ]


# Every row has a calibration question, an unseen paraphrase, and its expected document.
PAIRS = [
    ("双十一订单优惠区间是多少？", "十一月大促折扣按商品合计的什么比例生成？", "double11"),
    ("十一月整体订单生成系数是多少？", "年度集中促销期间的订单抽样权重设成几？", "double11"),
    ("2025 年家电补贴何时结束？", "演示情景里十二月还延续家电同等补贴吗？", "appliances"),
    ("十二月家用电器的商品选择权重是多少？", "大促后家电回落场景采用什么选品系数？", "appliances"),
    ("年货节主要经营哪个品类？", "十二月备年货方案的重点商品类别是什么？", "food"),
    ("十二月食品生鲜生成权重是多少？", "1.4 对应哪个年货品类的演示选品设置？", "food"),
    ("九月手机数码有什么运营主题？", "换机需求和专题介绍出自哪次发布活动？", "phones"),
    ("手机数码九月的商品选择权重是多少？", "九月发布期数码品类用了几倍抽样系数？", "phones"),
    ("服饰鞋包十一月和十二月系数分别是多少？", "秋冬服饰计划里的两个上新月权重是什么？", "fashion"),
    (
        "冬季服饰方案怎样看待订单实收和明细成交额？",
        "为什么秋冬上新不能把品类明细求和直接替代净实收？",
        "fashion",
    ),
    (
        "大促月的退款抽样概率是多少？",
        "客服汇总中非大促月和大促月退款状态概率有什么变化？",
        "refunds",
    ),
    (
        "客服汇总能算出每种退货原因的精确占比吗？",
        "想知道某类客诉占全部退款多少，需要补充什么数据？",
        "refunds",
    ),
    ("二月春节订单生成系数是多少？", "春节期间的演示订单量采用什么季节权重？", "spring"),
    ("春节月份比较为何要看日均订单？", "一月二月总订单量对比还要考虑什么时间差异？", "spring"),
    ("业务库金额如何换算成元？", "项目订单的最小金额单位是什么？", "metrics"),
    (
        "净销售额保留哪些状态并排除什么用户？",
        "为什么取消退款订单和测试账号不能计入净实收？",
        "metrics",
    ),
    ("华东和华北分别用什么区域代码？", "EAST、NORTH 对应哪些中文区域？", "regions"),
    (
        "下单端与获客来源有什么不同？",
        "app、web 和 ads、referral 为什么不能混作一个渠道？",
        "regions",
    ),
    ("标准配送承诺为几个工作日？", "额外合成服务约定里发货后通常多久送达？", "delivery"),
    ("偏远地区配送承诺是多少？", "偏远地址在这份合成物流约定里需要等几个工作日？", "delivery"),
]
NEGATIVE_PAIRS = [
    ("当前仓库的库存总金额是多少？", "每个仓的实时库存数量是多少？"),
    ("哪个供应商返利比例最高？", "年度采购合同的付款条件是什么？"),
    ("手机品牌今年市场份额各占多少？", "竞争对手最近一个月销售额是多少？"),
    ("今天上海的实时气温是多少？", "明天北京是否会下雨？"),
    ("火星轨道离心率的精确数值是什么？", "月球近地点距离为多少千米？"),
    ("客户张先生的家庭住址是什么？", "会员王女士的身份证号码是什么？"),
    ("客服员工的月工资分别是多少？", "本季度员工薪资总额是多少？"),
    ("最近一次董事会通过什么利润分红方案？", "今年股东每股获得多少现金分红？"),
    ("每台物流车辆当前坐标是什么？", "跨境包裹现在到哪个海关了？"),
    ("生产数据库的管理员密码是什么？", "聊天服务商账户里还剩多少余额？"),
]


def cases():
    result = []
    for number, (calibration, holdout, expected) in enumerate(
        PAIRS + [(a, b, None) for a, b in NEGATIVE_PAIRS]
    ):
        for split, query in (("calibration", calibration), ("holdout", holdout)):
            result.append(
                {
                    "id": f"{split}-{number:02d}",
                    "split": split,
                    "query": query,
                    "expected": expected,
                }
            )
    return result


# Fresh confirmation set authored after the first holdout, before gate validation.
# No further threshold search is permitted on this set; 0.6 is locked.
CONFIRMATION = [
    ("双十一期间的优惠金额是怎样按比例设定的？", "double11"),
    ("家电周报里提到的临时补贴在哪个日期终止？", "appliances"),
    ("年货备货内容运营主要围绕什么商品？", "food"),
    ("哪份复盘提到九月份的换机专题？", "phones"),
    ("秋冬上新在十一月和十二月分别采用多大品类权重？", "fashion"),
    ("从工单主题能直接得到各退款原因的比例吗？", "refunds"),
    ("二月份订单权重低于一月份的设定是什么？", "spring"),
    ("计算实收时要排除哪些订单与测试用户？", "metrics"),
    ("小程序订单和广告获客分别存在哪个渠道字段？", "regions"),
    ("服务约定对偏远地区送达时间是如何表述的？", "delivery"),
    ("今天华东仓库盘亏了多少件商品？", None),
    ("本周每位配送员的准时送达率是多少？", None),
    ("某手机品牌的售后故障率是多少？", None),
    ("今年双十一广告预算最终花了多少元？", None),
    ("这次年货活动的净利润实际是多少？", None),
    ("冬季服饰退货中尺码不合适具体占比多少？", None),
    ("家电补贴是哪位运营经理审批的？", None),
    ("客服工单的平均首次回复用了多少分钟？", None),
    ("春节期间每一天的仓库开门时间是什么？", None),
    ("九月发布会各手机品牌的具体广告点击率是多少？", None),
]


def confirmation_cases():
    return [
        {
            "id": f"confirmation-{number:02d}",
            "split": "holdout",
            "query": query,
            "expected": expected,
        }
        for number, (query, expected) in enumerate(CONFIRMATION)
    ]
