"""Read line-level sales CSV and generate an offline HTML dashboard and JSON."""
from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = {"line_id", "order_id", "paid_date", "buyer_id", "product_id", "product_name",
          "quantity", "paid_amount", "refund_amount", "status", "currency"}
PAID = {"paid", "completed", "partially_refunded", "refunded"}
UNPAID = {"unpaid", "cancelled"}


def money(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
        if not value.is_finite() or value < 0 or value != value.quantize(Decimal("0.01")):
            raise ValueError
        return value
    except (InvalidOperation, ValueError):
        raise ValueError(f"金额必须是非负且最多两位小数：{raw!r}") from None


def amount(value: Decimal) -> str:
    return f"{value:.2f}"


def iso_day(raw: str) -> date:
    value = date.fromisoformat(raw)
    if raw != value.isoformat():
        raise ValueError("日期必须是 YYYY-MM-DD")
    return value


def aggregate(source: Path, start: date, end: date, currency: str) -> dict:
    if end < start or (end - start).days > 3660:
        raise ValueError("日期范围必须有序且不超过3661天")
    total_paid, total_refund = Decimal(0), Decimal(0)
    orders, buyers, seen, order_metadata = set(), set(), set(), {}
    products, daily = {}, {}
    excluded, included, quantity = 0, 0, 0
    with source.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not FIELDS.issubset(reader.fieldnames or []):
            raise ValueError("CSV缺少字段：" + ", ".join(sorted(FIELDS - set(reader.fieldnames or []))))
        for line, raw in enumerate(reader, 2):
            try:
                row = {k: (raw[k] or "").strip() for k in FIELDS}
                if not row["line_id"] or row["line_id"] in seen:
                    raise ValueError("line_id为空或重复")
                seen.add(row["line_id"])
                if row["status"] not in PAID | UNPAID:
                    raise ValueError("未知订单状态，请先映射")
                if row["status"] in UNPAID:
                    excluded += 1
                    continue
                if any(not row[k] for k in FIELDS):
                    raise ValueError("已支付商品行存在空字段")
                day = iso_day(row["paid_date"])
                identity = (row["paid_date"], row["buyer_id"], row["currency"])
                previous = order_metadata.setdefault(row["order_id"], identity)
                if previous != identity:
                    raise ValueError("同订单的支付日、买家或币种不一致")
                paid, refund = money(row["paid_amount"]), money(row["refund_amount"])
                units = int(row["quantity"])
                if units <= 0 or refund > paid:
                    raise ValueError("件数必须为正整数，退款不得大于实收")
                if not start <= day <= end:
                    excluded += 1
                    continue
                if row["currency"] != currency:
                    raise ValueError("范围内包含其他币种，请先分币种导出，不进行自动汇率换算")
                net = paid - refund
                total_paid += paid
                total_refund += refund
                quantity += units
                included += 1
                orders.add(row["order_id"])
                buyers.add(row["buyer_id"])
                product = products.setdefault(row["product_id"], {
                    "product_id": row["product_id"], "name": row["product_name"],
                    "quantity": 0, "paid": Decimal(0), "refund": Decimal(0), "net": Decimal(0)})
                product["quantity"] += units
                product["paid"] += paid
                product["refund"] += refund
                product["net"] += net
                bucket = daily.setdefault(day, {"paid": Decimal(0), "refund": Decimal(0), "net": Decimal(0)})
                bucket["paid"] += paid
                bucket["refund"] += refund
                bucket["net"] += net
            except (ValueError, KeyError) as exc:
                raise ValueError(f"CSV第{line}行：{exc}") from None
    trend = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        bucket = daily.get(day, {"paid": Decimal(0), "refund": Decimal(0), "net": Decimal(0)})
        trend.append({"date": day.isoformat(), **{k: amount(v) for k, v in bucket.items()}})
    ranked = sorted(products.values(), key=lambda item: (-item["net"], item["product_id"]))
    return {"currency": currency, "start": start.isoformat(), "end": end.isoformat(),
            "scope": "按支付日期筛选；累计退款归属原支付日；数据为输入文件快照。",
            "included_lines": included, "excluded_lines": excluded,
            "summary": {"paid": amount(total_paid), "refund": amount(total_refund),
                        "net": amount(total_paid - total_refund), "orders": len(orders),
                        "buyers": len(buyers), "quantity": quantity,
                        "aov": amount(total_paid / len(orders)) if orders else None},
            "daily": trend, "products": [
                {k: amount(v) if isinstance(v, Decimal) else v for k, v in item.items()}
                for item in ranked]}


def bars(items: list[tuple[str, str]]) -> str:
    if not items:
        return '<p class="muted">暂无数据</p>'
    maximum = max((Decimal(value) for _, value in items), default=Decimal(0)) or Decimal(1)
    height = len(items) * 36 + 8
    body = []
    for i, (label, value) in enumerate(items):
        y, width = i * 36 + 8, float(Decimal(value) / maximum * 390)
        body.append(f'<text x="0" y="{y + 15}">{html.escape(label[:18])}</text>'
                    f'<rect x="175" y="{y}" width="{width:.2f}" height="22" rx="4"/>'
                    f'<text x="580" y="{y + 15}">{html.escape(value)}</text>')
    return f'<svg role="img" aria-label="净销售额对比图" viewBox="0 0 710 {height}">{"".join(body)}</svg>'


def render(report: dict, title: str) -> str:
    escape = lambda value: html.escape(str(value)) if value is not None else "—"
    summary = report["summary"]
    cards = "".join(f'<div class="card"><span>{label}</span><strong>{escape(summary[key]) if summary[key] is not None else "—"}</strong></div>'
                    for key, label in [("paid", "实收销售额"), ("refund", "订单归属退款额"), ("net", "净销售额"),
                                       ("orders", "支付订单数"), ("buyers", "购买人数"), ("aov", "实收客单价"), ("quantity", "支付件数")])
    rows = "".join("<tr>" + "".join(f"<td>{escape(p[k])}</td>" for k in ("product_id", "name", "quantity", "paid", "refund", "net")) + "</tr>"
                   for p in report["products"])
    trend = bars([(d["date"], d["net"]) for d in report["daily"]])
    ranking = bars([(p["name"], p["net"]) for p in report["products"][:10]])
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f5fa;color:#19233b;font:15px system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1200px;margin:auto;padding:32px 20px}}h1{{margin-bottom:8px}}h2{{font-size:20px}}.muted,.card span{{color:#64748b}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin:24px 0}}.card,section{{background:white;border:1px solid #e2e7f0;border-radius:14px;padding:20px}}.card strong{{display:block;font-size:25px;margin-top:10px}}section{{margin-top:18px}}.scroll{{overflow:auto;max-height:540px}}svg{{display:block;min-width:600px;width:100%;max-width:900px}}svg text{{font-size:13px;fill:#475569}}svg rect{{fill:#6d5ce7}}table{{width:100%;border-collapse:collapse;white-space:nowrap}}td,th{{padding:12px;border-bottom:1px solid #eef0f5;text-align:right}}td:first-child,td:nth-child(2),th:first-child,th:nth-child(2){{text-align:left}}@media(max-width:600px){{main{{padding:20px 12px}}.card,section{{padding:14px}}}}
</style><main><h1>{escape(title)}</h1><p class="muted">{report['start']} 至 {report['end']} · 金额单位：{escape(report['currency'])}</p>
<p>{escape(report['scope'])}</p><div class="cards">{cards}</div>
<section><h2>每日净销售额</h2><div class="scroll">{trend}</div></section>
<section><h2>商品净销售额 Top 10</h2><div class="scroll">{ranking}</div></section>
<section><h2>商品销售明细</h2><div class="scroll"><table><thead><tr><th>商品ID</th><th>商品</th><th>支付件数</th><th>实收</th><th>退款</th><th>净额</th></tr></thead><tbody>{rows}</tbody></table></div>{'' if rows else '<p class="muted">所选区间暂无销售数据</p>'}</section>
<p class="muted">纳入 {report['included_lines']} 行；排除未支付、取消或区间外 {report['excluded_lines']} 行。客单价=实收/支付订单；退款商品仍计入支付件数；不含利润和转化率推算。</p></main></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="生成电商销售离线看板（订单归属退款口径）")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=iso_day, required=True)
    parser.add_argument("--end", type=iso_day, required=True)
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--title", default="电商销售统计")
    args = parser.parse_args()
    try:
        report = aggregate(args.source, args.start, args.end, args.currency)
        output_paths = [args.output / "sales-report.json", args.output / "sales-report.html"]
        if any(path.resolve() == args.source.resolve() or path.exists() for path in output_paths):
            raise ValueError("输出文件已存在或与输入冲突，请选择一个新的输出目录")
        args.output.mkdir(parents=True, exist_ok=True)
        output_paths[0].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        output_paths[1].write_text(render(report, args.title), encoding="utf-8")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"生成失败：{exc}\n")
    print(f"已生成：{output_paths[1]}")


if __name__ == "__main__":
    main()
