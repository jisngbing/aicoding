"""基于同一支付订单集合计算品类、退款指标与有数据依据的经营建议。"""
from __future__ import annotations

from decimal import Decimal

from agent.core.sales_report import PAID, amount


def ratio(numerator, denominator):
    return format(numerator / denominator, ".6f") if denominator else None


def _group():
    return {"paid": Decimal(0), "refund": Decimal(0), "orders": set(), "refunded_orders": set()}


def enrich_report(report: dict, normalized: list[dict], source_rows: list[dict], mapping: dict) -> None:
    categories, products = {}, {}
    orders, refunded_orders = set(), set()
    covered, unknown = 0, 0
    for row, original in zip(normalized, source_rows):
        if row["status"] not in PAID or not report["start"] <= row["paid_date"] <= report["end"]:
            continue
        paid, refund = Decimal(row["paid_amount"]), Decimal(row["refund_amount"])
        order = row["order_id"]
        orders.add(order)
        if refund > 0:
            refunded_orders.add(order)
        category = original.get(mapping.get("category"), "").strip()
        covered += bool(category)
        unknown += not bool(category)
        targets = [(products, row["product_id"], row["product_name"])]
        if "category" in mapping:
            targets.append((categories, category or "未分类", category or "未分类"))
        for groups, key, name in targets:
            group = groups.setdefault(key, {"name": name, **_group()})
            group["paid"] += paid
            group["refund"] += refund
            group["orders"].add(order)
            if refund > 0:
                group["refunded_orders"].add(order)

    total_paid, total_refund = Decimal(report["summary"]["paid"]), Decimal(report["summary"]["refund"])

    def serialize(groups):
        result = []
        for key, group in groups.items():
            result.append({"id": key, "name": group["name"], "paid": amount(group["paid"]),
                           "refund": amount(group["refund"]), "net": amount(group["paid"] - group["refund"]),
                           "sales_share": ratio(group["paid"], total_paid),
                           "refund_rate": ratio(group["refund"], group["paid"]),
                           "orders": len(group["orders"]), "refunded_orders": len(group["refunded_orders"])})
        return sorted(result, key=lambda item: (-Decimal(item["net"]), item["id"]))

    category_rows, product_rows = serialize(categories), serialize(products)
    product_index = {item["id"]: item for item in product_rows}
    for product in report["products"]:
        stats = product_index[product["product_id"]]
        product.update({key: stats[key] for key in ("orders", "refunded_orders", "refund_rate")})
    report["category_coverage"] = {"available": "category" in mapping, "covered_lines": covered,
                                   "unknown_lines": unknown, "total_categories": len(category_rows)}
    report["categories"] = category_rows[:100]
    report["refund_analysis"] = {
        "amount": amount(total_refund), "amount_rate": ratio(total_refund, total_paid),
        "refunded_orders": len(refunded_orders), "paid_orders": len(orders),
        "order_rate": ratio(Decimal(len(refunded_orders)), Decimal(len(orders))),
        "assumed_no_refunds": "refund_amount" not in mapping,
        "top_products": sorted((p for p in product_rows if Decimal(p["refund"]) > 0),
                               key=lambda p: (-Decimal(p["refund"]), p["id"]))[:5],
        "top_categories": sorted((c for c in category_rows if Decimal(c["refund"]) > 0),
                                 key=lambda c: (-Decimal(c["refund"]), c["id"]))[:5],
    }
    report["recommendations"] = recommendations(report, category_rows)
    if "category" not in mapping:
        report["notes"].append("未提供品类列，暂不生成品类结构与品类经营建议；可映射category后重新分析。")
    elif unknown:
        report["notes"].append(f"{unknown}条纳入明细缺少品类，计入未分类；品类订单数存在跨品类交叉，不能直接相加。")
    else:
        report["notes"].append("同一订单可包含多个品类，品类订单数不能直接相加。")
    if len(category_rows) > 100:
        report["notes"].append("品类明细展示净额前100类；占比基于全店实收，图表将未展示项计入其他。")
    report["notes"].append("金额退款率=累计退款/同批订单实收；退款订单率=发生退款的去重订单数/支付订单数。退款原因、利润和库存未在本报表中验证。")


def recommendations(report: dict, categories: list[dict]) -> list[dict]:
    currency, summary = report["currency"], report["summary"]
    refunds = report["refund_analysis"]
    output = []
    if not summary["orders"]:
        return [{"id": "empty", "topic": "数据", "priority": "待核查", "title": "当前区间没有已支付订单",
                 "evidence": f"{report['start']}至{report['end']}支付订单数为0。",
                 "actions": ["检查日期筛选、支付时间和订单状态映射，再进行销售决策。"], "measure": "有效支付订单数"}]
    known = [item for item in categories if item["name"] != "未分类" and Decimal(item["paid"]) > 0]
    if known and any(Decimal(item["net"]) > 0 for item in known):
        leading = max(known, key=lambda c: Decimal(c["net"]))
        share = Decimal(leading["sales_share"] or "0") * 100
        output.append({"id": "category-focus", "topic": "品类", "priority": "优先复盘",
                       "title": f"优先复盘「{leading['name']}」的供给与商品组合",
                       "evidence": f"该品类实收{leading['paid']} {currency}，占全店实收{share:.1f}%；扣除退款后净额{leading['net']} {currency}。",
                       "actions": ["核对该品类畅销SKU的库存覆盖和缺货记录，优先保障已验证的销售需求。",
                                   "结合毛利与连带购买记录，小范围验证补货或组合销售，再决定资源投入。"],
                       "measure": "品类净销售额、毛利、缺货率与金额退款率"})
    if Decimal(refunds["amount"]) > 0:
        candidates = refunds["top_categories"] or refunds["top_products"]
        if candidates:
            risk = candidates[0]
            rate = Decimal(risk["refund_rate"] or "0") * 100
            refund_share = Decimal(risk["refund"]) / Decimal(refunds["amount"]) * 100
            output.append({"id": "refund-focus", "topic": "退款", "priority": "优先排查",
                           "title": f"优先排查「{risk['name']}」的退款损失",
                           "evidence": f"累计退款{risk['refund']} {currency}，占全店退款{refund_share:.1f}%；该组金额退款率{rate:.1f}%，涉及{risk['refunded_orders']}笔退款订单。",
                           "actions": ["按售后原因、SKU/规格、批次和物流环节核查，区分质量、描述不符与履约问题。",
                                       "根据核查结果调整详情页规格说明、质检或包装，并对照同成熟度订单队列复查退款表现。"],
                           "measure": "该组退款金额、金额退款率及净销售额；排查方向不代表已确认原因"})
    elif refunds["assumed_no_refunds"]:
        output.append({"id": "refund-missing", "topic": "退款", "priority": "补充数据", "title": "补齐实际售后数据再评估退款风险",
                       "evidence": "CSV没有退款列，当前0退款来自用户确认的无退款假设。",
                       "actions": ["后续导出累计已完成退款金额和售后原因，再判断品类及商品的退款差异。"],
                       "measure": "退款数据覆盖率"})
    else:
        output.append({"id": "refund-zero", "topic": "退款", "priority": "持续观察", "title": "继续跟踪本批订单的售后表现",
                       "evidence": f"当前输入快照的{summary['orders']}笔支付订单累计退款为0。",
                       "actions": ["在售后窗口结束后复查这批订单，避免把尚未发生的退款当作最终表现。"],
                       "measure": "同批订单售后成熟后的金额退款率"})
    daily = report["daily"]
    if len(daily) >= 4:
        length = len(daily) // 2
        first, last = daily[:length], daily[-length:]
        earlier = sum((Decimal(d["net"]) for d in first), Decimal(0))
        recent = sum((Decimal(d["net"]) for d in last), Decimal(0))
        if earlier > 0 and recent != earlier:
            change = (recent - earlier) / earlier * 100
            output.append({"id": "sales-trend", "topic": "销售", "priority": "跟踪验证", "title": "复盘区间内销售变化",
                           "evidence": f"后{length}天（{last[0]['date']}至{last[-1]['date']}）净额{amount(recent)} {currency}，相比前{length}天（{first[0]['date']}至{first[-1]['date']}）变化{change:+.1f}%。这是区间内等长对比，不是同比。",
                           "actions": ["对照促销、流量、库存与退款记录定位变化来源，先验证具体渠道或SKU再调整经营策略。"],
                           "measure": "等长区间净销售额、流量、库存与退款；当前数据不能证明变化原因"})
    if not known:
        output.append({"id": "category-missing", "topic": "品类", "priority": "补充数据", "title": "补充品类信息后再制定品类策略",
                       "evidence": "当前CSV缺少可用的品类归属，无法判断品类贡献及品类退款差异。",
                       "actions": ["为每条订单商品明细补充category或品类列，并在上传界面确认字段映射。"],
                       "measure": "品类归属覆盖率"})
    return output
