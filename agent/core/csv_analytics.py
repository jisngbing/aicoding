"""用户 CSV 的有界解析、字段映射和离线销售看板；不访问仓库或执行上传内容。"""
from __future__ import annotations

import csv
import html
import io
import json
import re
import tempfile
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from agent.core import sales_report, settings
from agent.core.sales_insights import enrich_report

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 80
FIELDS = {
    "order_id": ("订单号", "订单编号", "订单ID", "order_no", "order_number"),
    "line_id": ("明细ID", "明细编号", "子订单号", "订单商品行ID", "order_item_id"),
    "paid_date": ("支付日期", "支付时间", "付款时间", "paid_at", "payment_time"),
    "paid_amount": ("实收金额", "实付金额", "商品实付金额", "订单实付金额", "订单金额", "payment_amount", "amount"),
    "refund_amount": ("退款金额", "累计退款金额", "refund"),
    "buyer_id": ("买家ID", "用户ID", "客户ID", "user_id", "customer_id"),
    "product_id": ("商品ID", "商品编号", "sku_id", "sku"),
    "product_name": ("商品名称", "商品名", "product", "sku_name"),
    "quantity": ("数量", "商品数量", "购买数量", "qty"),
    "status": ("订单状态", "支付状态", "order_status", "payment_status"),
    "currency": ("币种", "货币"),
    "category": ("品类", "类目", "商品分类", "category_name"),
    "channel": ("渠道", "销售渠道", "平台", "sales_channel"),
}
STATUS = {
    "已支付": "paid", "支付成功": "paid", "已付款": "paid",
    "已完成": "completed", "交易成功": "completed", "已发货": "completed", "已收货": "completed",
    "部分退款": "partially_refunded", "全额退款": "refunded", "已退款": "refunded",
    "未支付": "unpaid", "待支付": "unpaid", "待付款": "unpaid", "已取消": "cancelled", "canceled": "cancelled",
}
ARTIFACTS = {"dashboard.html", "report.json", "summary.csv", "products.csv", "daily.csv", "categories.csv"}


class CsvAnalysisError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise CsvAnalysisError("文件或报表编号无效")
    return value


def _key(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value).lower()


def parse_csv(data: bytes) -> tuple[list[str], list[dict], str]:
    if not data or len(data) > MAX_BYTES:
        raise CsvAnalysisError("CSV不能为空，且最多10MB", 413 if data else 400)
    text, encoding = "", ""
    for candidate in ("utf-8-sig", "gb18030"):
        try:
            text, encoding = data.decode(candidate), candidate
            break
        except UnicodeDecodeError:
            continue
    if not encoding or "\0" in text:
        raise CsvAnalysisError("请上传 UTF-8 或 GB18030 编码的文本 CSV")
    try:
        try:
            dialect = csv.Sniffer().sniff(text[:16384], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text, newline=""), dialect, strict=True)
        header = [name.strip() for name in next(reader, [])]
        if not 2 <= len(header) <= MAX_COLUMNS or any(not c or len(c) > 100 for c in header):
            raise CsvAnalysisError("CSV需包含2～80列，表头不能为空且每列不超过100字符")
        if len({_key(c) for c in header}) != len(header):
            raise CsvAnalysisError("CSV表头重复，请先为每列指定唯一名称")
        rows = []
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(rows) >= MAX_ROWS:
                raise CsvAnalysisError("CSV最多支持50000条数据，请拆分文件", 413)
            if len(row) != len(header) or any(len(cell) > 8192 for cell in row):
                raise CsvAnalysisError(f"CSV第{reader.line_num}行列数不匹配或单元格过长")
            rows.append(dict(zip(header, (cell.strip() for cell in row))))
        if not rows:
            raise CsvAnalysisError("CSV只有表头，没有数据")
        return header, rows, encoding
    except csv.Error:
        raise CsvAnalysisError("CSV格式不正确，请检查分隔符、引号及单元格长度") from None


def infer_fields(columns: list[str]) -> dict[str, str]:
    result = {}
    for target, aliases in FIELDS.items():
        if target in columns:
            result[target] = target
            continue
        matches = [column for column in columns if _key(column) in {_key(a) for a in (target, *aliases)}]
        if len(matches) == 1:
            result[target] = matches[0]
    return result


def _paid_day(value: str, row: int) -> str:
    try:
        value = value.replace("/", "-")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone(timedelta(hours=8)))
        return parsed.date().isoformat()
    except ValueError:
        raise CsvAnalysisError(f"第{row}行支付日期无效，请用 YYYY-MM-DD 或 ISO 时间；业务时区为 Asia/Shanghai") from None


def _normalize(columns, rows, field_map, currency, granularity, assume_paid, assume_no_refunds):
    mapping = infer_fields(columns)
    if field_map:
        if any(key not in FIELDS or (value and value not in columns) for key, value in field_map.items()):
            raise CsvAnalysisError("字段映射含未知字段或不存在的CSV列")
        for key, value in field_map.items():
            if value:
                mapping[key] = value
            else:
                mapping.pop(key, None)
    if len(set(mapping.values())) != len(mapping):
        raise CsvAnalysisError("同一CSV列不能映射到多个统计字段")
    missing = [field for field in ("order_id", "paid_date", "paid_amount") if field not in mapping]
    if missing:
        raise CsvAnalysisError("缺少必需字段映射：" + ", ".join(missing))
    if "status" not in mapping and not assume_paid:
        raise CsvAnalysisError("缺少订单状态；若文件全是已支付订单，请明确设置 assume_paid=true")
    if "refund_amount" not in mapping and not assume_no_refunds:
        raise CsvAnalysisError("缺少退款金额；若确认没有退款，请设置 assume_no_refunds=true，否则先补充退款数据")
    if granularity not in {"auto", "order", "line"} or not re.fullmatch(r"[A-Z]{3}", currency):
        raise CsvAnalysisError("granularity应为auto/order/line，currency应为三位大写币种代码")
    notes = ["业务时区：Asia/Shanghai；按支付日期筛选，累计退款归属原支付日。"]
    if "status" not in mapping:
        notes.append("用户确认输入均为已支付记录。")
    if "refund_amount" not in mapping:
        notes.append("用户确认无退款，退款金额按0统计。")
    for key, label in (("buyer_id", "购买人数"), ("quantity", "支付件数")):
        if key not in mapping:
            notes.append(f"缺少{key}，{label}不可计算。")
    if "product_id" not in mapping:
        notes.append("无商品ID，以商品名称分组；同名商品可能合并。" if "product_name" in mapping else "无商品字段，统一显示为未分类，不代表真实商品排行。")
    if "currency" not in mapping:
        notes.append(f"无币种列，按用户指定的{currency}统计，未进行汇率换算。")
    normalized, unique, paid_orders, paid_days = [], set(), set(), []
    for i, row in enumerate(rows, 2):
        fingerprint = row.get(mapping.get("line_id")) if "line_id" in mapping else tuple(row.values())
        if not fingerprint or fingerprint in unique:
            raise CsvAnalysisError(f"第{i}行明细ID为空、重复或存在完全重复行，请核对数据")
        unique.add(fingerprint)
        value = lambda key, default="", row=row: row[mapping[key]] if key in mapping else default
        status = STATUS.get(value("status").lower(), value("status").lower()) if "status" in mapping else "paid"
        if status not in sales_report.PAID | sales_report.UNPAID:
            raise CsvAnalysisError(f"第{i}行订单状态无法识别，请先映射到paid/completed/refunded/partially_refunded/unpaid/cancelled")
        order_id = value("order_id")
        if not order_id:
            raise CsvAnalysisError(f"第{i}行订单ID为空")
        day = ""
        if status in sales_report.PAID:
            day = _paid_day(value("paid_date"), i)
            paid_days.append(day)
            if order_id in paid_orders:
                if granularity == "order":
                    raise CsvAnalysisError("订单粒度下订单号重复，不能重复累计整单金额")
                if granularity == "auto" and ("line_id" not in mapping or "订单" in mapping["paid_amount"]):
                    raise CsvAnalysisError("检测到多行同订单，请确认金额为每行商品实收，选择line粒度；整单金额重复时应先整理成一行一订单")
            paid_orders.add(order_id)
        normalized.append({"line_id": str(i), "order_id": order_id, "paid_date": day,
                           "buyer_id": value("buyer_id", "unknown"),
                           "product_id": value("product_id", value("product_name", "unclassified")),
                           "product_name": value("product_name", value("product_id", "未分类")),
                           "quantity": value("quantity", "1"), "paid_amount": value("paid_amount"),
                           "refund_amount": value("refund_amount", "0"), "status": status,
                           "currency": value("currency", currency)})
    return normalized, mapping, notes, paid_days


def _csv_bytes(matrix: list[list]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    # 防止商品名等用户文本在 Excel 中作为公式执行；不修改源CSV。
    for row in matrix:
        writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value for value in row])
    return stream.getvalue().encode("utf-8-sig")


class CsvAnalytics:
    def __init__(self, root: Path | None = None):
        self.root = (root or settings.DATA_DIR / "csv_analytics").resolve()

    def upload(self, data: bytes, filename: str) -> dict:
        if not filename.lower().endswith(".csv"):
            raise CsvAnalysisError("只支持.csv文件，请先将Excel导出为CSV")
        columns, rows, encoding = parse_csv(data)
        file_id = uuid.uuid4().hex
        directory = self.root / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{file_id}.csv").write_bytes(data)
        return {"file_id": file_id, "columns": columns, "row_count": len(rows),
                "encoding": encoding, "field_map": infer_fields(columns), "max_rows": MAX_ROWS}

    def analyze(self, file_id: str, *, field_map: dict[str, str] | None = None,
                start_date: str = "", end_date: str = "", currency: str = "CNY",
                granularity: str = "auto", assume_paid: bool = False, assume_no_refunds: bool = False) -> dict:
        source = self.root / "uploads" / f"{_identifier(file_id)}.csv"
        if not source.is_file() or source.is_symlink():
            raise CsvAnalysisError("CSV文件不存在，请重新上传", 404)
        with source.open("rb") as file:
            columns, rows, _ = parse_csv(file.read(MAX_BYTES + 1))
        normalized, mapping, notes, paid_days = _normalize(
            columns, rows, field_map, currency, granularity, assume_paid, assume_no_refunds)
        try:
            start = sales_report.iso_day(start_date or min(paid_days, default=date.today().isoformat()))
            end = sales_report.iso_day(end_date or max(paid_days, default=start.isoformat()))
        except ValueError:
            raise CsvAnalysisError("统计起止日期必须为YYYY-MM-DD") from None
        reports = self.root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        report_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(dir=reports, prefix=".build-") as temporary:
            stage = Path(temporary)
            normalized_file = stage / "normalized.csv"
            with normalized_file.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=sorted(sales_report.FIELDS))
                writer.writeheader()
                writer.writerows(normalized)
            try:
                report = sales_report.aggregate(normalized_file, start, end, currency)
            except ValueError as exc:
                # 错误可能包含CSV原始字段值，只返回行号和校验类别，不回显内容。
                match = re.search(r"CSV第(\d+)行", str(exc))
                hint = f"第{match[1]}行校验失败：" if match else "统计校验失败："
                raise CsvAnalysisError(hint + "请检查日期范围、币种、同订单买家一致性及金额/数量（非负金额、正整数件数、退款不超过实收）。") from None
            if "buyer_id" not in mapping:
                report["summary"]["buyers"] = None
            if "quantity" not in mapping:
                report["summary"]["quantity"] = None
                for product in report["products"]:
                    product["quantity"] = None
            report["notes"], report["field_map"] = notes, mapping
            enrich_report(report, normalized, rows, mapping)
            report["schema_version"] = 2
            report["generated_at"] = datetime.now(UTC).isoformat()
            report["source_file_id"] = file_id
            report["data_label"] = "演示数据" if all(row.get("data_type") == "演示数据" for row in rows) else "上传数据"
            report["analysis_options"] = {"file_id": file_id, "field_map": {**mapping, **(field_map or {})}, "currency": currency,
                                          "granularity": granularity, "assume_paid": assume_paid,
                                          "assume_no_refunds": assume_no_refunds}
            report["product_count"] = len(report["products"])
            if len(report["products"]) > 100:
                notes.append("商品明细仅展示净额前100名，总计包含全部商品。")
            report["products"] = report["products"][:100]
            page = sales_report.render(report, "电商销售统计看板")
            notices = '<section><h2>统计口径与数据说明</h2><ul>' + ''.join(f'<li>{html.escape(note)}</li>' for note in notes) + '</ul></section>'
            notices += '<section><h2>销售与退款经营建议</h2>' + ''.join(
                '<h3>' + html.escape(item["title"]) + '</h3><p>' + html.escape(item["evidence"]) + '</p><ul>'
                + ''.join('<li>' + html.escape(action) + '</li>' for action in item["actions"]) + '</ul>'
                for item in report["recommendations"]) + '</section>'
            page = page.replace("</main>", notices + "</main>")
            output = stage / "output"
            output.mkdir()
            (output / "dashboard.html").write_text(page, encoding="utf-8")
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            labels = {"paid": "实收销售额", "refund": "订单归属退款额", "net": "净销售额", "orders": "支付订单数",
                      "buyers": "购买人数", "quantity": "支付件数", "aov": "实收客单价"}
            (output / "summary.csv").write_bytes(_csv_bytes([["指标", "数值", "币种"], *[
                [labels[key], value if value is not None else "不可计算",
                 {"orders": "单", "buyers": "人", "quantity": "件", "aov": currency + "/单"}.get(key, currency)]
                for key, value in report["summary"].items()]]))
            for name, keys in (("daily", ["date", "paid", "refund", "net"]),
                               ("products", ["product_id", "name", "quantity", "paid", "refund", "net", "refund_rate"]),
                               ("categories", ["name", "paid", "refund", "net", "sales_share", "refund_rate", "orders"])):
                (output / f"{name}.csv").write_bytes(_csv_bytes([keys, *[
                    [item[k] if item[k] is not None else "不可计算" for k in keys] for item in report[name]]]))
            output.rename(reports / report_id)
        return self._payload(report_id, report)

    @staticmethod
    def _payload(report_id: str, report: dict) -> dict:
        prefix = f"/dashboard/api/csv/reports/{report_id}"
        return {**report, "ok": True, "report_id": report_id,
                "artifacts": {"dashboard": prefix + "/dashboard.html", "json": prefix + "/report.json",
                              "summary_csv": prefix + "/summary.csv", "products_csv": prefix + "/products.csv",
                              "daily_csv": prefix + "/daily.csv",
                              **({"categories_csv": prefix + "/categories.csv"} if report.get("schema_version") == 2 else {})}}

    def get_report(self, report_id: str) -> dict:
        path = self.artifact(report_id, "report.json")
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError
            report = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(report, dict) or not isinstance(report.get("summary"), dict):
                raise TypeError
        except (ValueError, TypeError, UnicodeError):
            raise CsvAnalysisError("报表内容不可读，请重新分析CSV", 422) from None
        if report.get("schema_version") != 2:
            report.setdefault("notes", []).append("这是历史报表，请重新上传CSV启用品类分析、退款诊断和经营建议。")
        return self._payload(report_id, report)

    def latest(self) -> dict | None:
        directory = self.root / "reports"
        if not directory.is_dir():
            return None
        candidates = [path for path in directory.iterdir()
                      if re.fullmatch(r"[a-f0-9]{32}", path.name) and path.is_dir() and not path.is_symlink()]
        for path in sorted(candidates, key=lambda p: p.stat().st_mtime_ns, reverse=True)[:20]:
            try:
                return self.get_report(path.name)
            except (CsvAnalysisError, OSError):
                continue
        return None

    def artifact(self, report_id: str, filename: str) -> Path:
        if filename not in ARTIFACTS:
            raise CsvAnalysisError("报表文件不存在", 404)
        path = self.root / "reports" / _identifier(report_id) / filename
        if not path.is_file() or path.is_symlink():
            raise CsvAnalysisError("报表文件不存在", 404)
        return path
