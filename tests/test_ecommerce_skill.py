import csv
import importlib.util
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "skills" / "ecommerce-sales-analytics"
spec = importlib.util.spec_from_file_location("sales_report", ROOT / "scripts" / "sales_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def sample(tmp_path, change=None):
    with (ROOT / "assets" / "sales-template.csv").open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows, fields = list(reader), reader.fieldnames
    if change:
        change(rows)
    target = tmp_path / "sales.csv"
    with target.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target


def calculate(source, start="2026-10-01", end="2026-10-03"):
    return report.aggregate(source, date.fromisoformat(start), date.fromisoformat(end), "CNY")


def test_multiline_orders_refunds_and_daily_reconciliation(tmp_path):
    result = calculate(sample(tmp_path))
    assert result["summary"] == {"paid": "350.00", "refund": "100.00", "net": "250.00",
                                 "orders": 2, "buyers": 2, "quantity": 4, "aov": "175.00"}
    assert result["excluded_lines"] == 1
    assert [d["net"] for d in result["daily"]] == ["250.00", "0.00", "0.00"]
    assert sum(report.Decimal(p["net"]) for p in result["products"]) == report.Decimal("250.00")


@pytest.mark.parametrize("change,error", [
    (lambda rows: rows.append(rows[0].copy()), "line_id"),
    (lambda rows: rows[0].update(refund_amount="100.01"), "退款不得大于实收"),
    (lambda rows: rows[0].update(paid_amount="NaN"), "金额"),
    (lambda rows: rows[0].update(currency="USD"), "币种"),
    (lambda rows: rows[1].update(buyer_id="OTHER"), "同订单"),
    (lambda rows: rows[0].update(status="unknown"), "未知订单状态"),
])
def test_invalid_data_is_rejected_before_report(tmp_path, change, error):
    with pytest.raises(ValueError, match=error):
        calculate(sample(tmp_path, change))


def test_date_filter_empty_and_zero_denominator(tmp_path):
    source = sample(tmp_path)
    one_day = calculate(source, "2026-10-03", "2026-10-03")
    assert one_day["summary"]["orders"] == 1
    assert one_day["summary"]["refund"] == "50.00"
    empty = calculate(source, "2026-10-02", "2026-10-02")
    assert empty["summary"]["aov"] is None
    assert empty["summary"]["net"] == "0.00"
    assert "暂无销售数据" in report.render(empty, "销售统计")


def test_untrusted_product_name_and_title_are_escaped(tmp_path):
    result = calculate(sample(tmp_path, lambda rows: rows[0].update(product_name="<script>alert(1)</script>")))
    page = report.render(result, "<img src=x onerror=alert(1)>")
    assert "<script>" not in page and "<img" not in page
    assert "&lt;script&gt;" in page
    assert "<svg" in page and "<table" in page
