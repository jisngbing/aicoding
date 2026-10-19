import csv
import importlib
import io
import json
from pathlib import Path

import pytest

from agent.api import csv_routes
from agent.core.csv_analytics import (
    MAX_BYTES,
    CsvAnalysisError,
    CsvAnalytics,
    infer_fields,
    parse_csv,
)
from agent.core.task_intent import classify_task_kind

tool_module = importlib.import_module("agent.tools.csv_analysis")
SOURCE = Path(__file__).resolve().parents[1] / "skills/ecommerce-sales-analytics/assets/sales-template.csv"


def csv_bytes(rows, header=None, encoding="utf-8"):
    header = header or list(rows[0])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=header)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode(encoding)


def sample_rows():
    return list(csv.DictReader(io.StringIO(SOURCE.read_text(encoding="utf-8"))))


@pytest.fixture
def service(tmp_path):
    return CsvAnalytics(tmp_path / "analytics")


def test_upload_analysis_and_exports_reconcile(service):
    uploaded = service.upload(SOURCE.read_bytes(), "orders.csv")
    assert uploaded["row_count"] == 4
    result = service.analyze(uploaded["file_id"])
    assert result["ok"]
    assert result["summary"] == {"paid": "350.00", "refund": "100.00", "net": "250.00", "orders": 2, "buyers": 2, "quantity": 4, "aov": "175.00"}
    assert result["excluded_lines"] == 1
    report = json.loads(service.artifact(result["report_id"], "report.json").read_text(encoding="utf-8"))
    assert [d["net"] for d in report["daily"]] == ["250.00", "0.00", "0.00"]
    assert report["summary"] == result["summary"]
    for filename in ("dashboard.html", "summary.csv", "products.csv", "daily.csv"):
        assert service.artifact(result["report_id"], filename).stat().st_size > 0
    assert len(list((service.root / "uploads").iterdir())) == 1


def test_chinese_gb18030_columns_and_utc_business_date(service):
    rows = [{"订单号": "O1", "支付时间": "2026-09-30T18:00:00Z", "实收金额": "123.45", "退款金额": "3.45", "订单状态": "已支付"}]
    uploaded = service.upload(csv_bytes(rows, encoding="gb18030"), "订单.csv")
    assert uploaded["field_map"]["order_id"] == "订单号"
    result = service.analyze(uploaded["file_id"])
    assert result["start"] == "2026-10-01" and result["summary"]["net"] == "120.00"
    assert result["summary"]["buyers"] is None and result["summary"]["quantity"] is None
    page = service.artifact(result["report_id"], "dashboard.html").read_text(encoding="utf-8")
    assert "None" not in page and "不可计算" in page


def test_unknown_header_explicit_mapping_and_confirmations(service):
    rows = [{"编号": "O1", "日期": "2026-10-01", "金额": "10.10"}]
    uploaded = service.upload(csv_bytes(rows), "orders.csv")
    with pytest.raises(CsvAnalysisError, match="必需字段"):
        service.analyze(uploaded["file_id"])
    mapping = {"order_id": "编号", "paid_date": "日期", "paid_amount": "金额"}
    with pytest.raises(CsvAnalysisError, match="状态"):
        service.analyze(uploaded["file_id"], field_map=mapping)
    with pytest.raises(CsvAnalysisError, match="退款"):
        service.analyze(uploaded["file_id"], field_map=mapping, assume_paid=True)
    result = service.analyze(uploaded["file_id"], field_map=mapping, assume_paid=True, assume_no_refunds=True)
    assert result["summary"]["net"] == "10.10"
    assert any("用户确认无退款" in note for note in result["notes"])


def test_explicit_empty_mapping_disables_automatic_match(service):
    uploaded = service.upload(SOURCE.read_bytes(), "orders.csv")
    result = service.analyze(uploaded["file_id"], field_map={"buyer_id": ""})
    assert result["summary"]["buyers"] is None
    with pytest.raises(CsvAnalysisError, match="必需字段"):
        service.analyze(uploaded["file_id"], field_map={"paid_amount": ""})


def test_duplicate_order_granularity_guard(service):
    rows = sample_rows()[:2]
    for row in rows:
        del row["line_id"]
    uploaded = service.upload(csv_bytes(rows), "orders.csv")
    for grain in ("auto", "order"):
        with pytest.raises(CsvAnalysisError, match="订单"):
            service.analyze(uploaded["file_id"], granularity=grain)
    assert service.analyze(uploaded["file_id"], granularity="line")["summary"]["orders"] == 1


@pytest.mark.parametrize("change", [
    lambda rows: rows.append(rows[0].copy()),
    lambda rows: rows[0].update(refund_amount="9999"),
    lambda rows: rows[0].update(quantity="-1"),
    lambda rows: rows[0].update(paid_amount="NaN"),
    lambda rows: rows[0].update(currency="USD"),
    lambda rows: rows[0].update(status="不确定"),
    lambda rows: rows[0].update(paid_date="not-a-date"),
])
def test_invalid_data_has_no_partial_reports(service, change):
    rows = sample_rows()
    change(rows)
    uploaded = service.upload(csv_bytes(rows), "orders.csv")
    with pytest.raises(CsvAnalysisError):
        service.analyze(uploaded["file_id"])
    assert not (service.root / "reports").exists() or not list((service.root / "reports").iterdir())


@pytest.mark.parametrize("data", [b"", b"x" * (MAX_BYTES + 1), b"a,a\n1,2", b"a,b\n1,2,3", b"a,b\n", b"a,b\n\x00,1"],
                         ids=["empty", "oversize", "duplicate-header", "extra-cell", "header-only", "binary"])
def test_invalid_uploads_are_rejected_without_persistence(service, data):
    with pytest.raises(CsvAnalysisError):
        service.upload(data, "orders.csv")
    assert not service.root.exists()


def test_csv_delimiter_and_ambiguous_aliases():
    columns, rows, _ = parse_csv("订单号;实收金额\nO1;10.00\n".encode())
    assert rows[0]["实收金额"] == "10.00"
    assert infer_fields(columns)["order_id"] == "订单号"
    assert "paid_amount" not in infer_fields(["实收金额", "实付金额"])


def test_empty_period_and_date_validation(service):
    uploaded = service.upload(SOURCE.read_bytes(), "orders.csv")
    result = service.analyze(uploaded["file_id"], start_date="2026-10-02", end_date="2026-10-02")
    assert result["summary"]["orders"] == 0 and result["summary"]["aov"] is None
    for options in ({"start_date": "bad"}, {"start_date": "2026-10-05", "end_date": "2026-10-01"}, {"start_date": "2000-01-01"}):
        with pytest.raises(CsvAnalysisError):
            service.analyze(uploaded["file_id"], **options)


def test_output_escaping_and_no_raw_buyer_data(service, monkeypatch):
    rows = sample_rows()
    rows[0]["product_name"] = "<script>alert(1)</script>"
    rows[1]["product_name"] = "=HYPERLINK(\"https://bad.example\")"
    result = service.analyze(service.upload(csv_bytes(rows), "orders.csv")["file_id"])
    page = service.artifact(result["report_id"], "dashboard.html").read_text(encoding="utf-8")
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "DEMO-U001" not in page
    content = service.artifact(result["report_id"], "products.csv").read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK" in content
    monkeypatch.setattr(csv_routes, "CsvAnalytics", lambda: service)
    response = csv_routes.report_file(result["report_id"], "dashboard.html")
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    for file_id in ("../.env", "C:\\secret", "https://example.com", "x" * 32):
        with pytest.raises(CsvAnalysisError):
            service.analyze(file_id)
    with pytest.raises(CsvAnalysisError):
        service.artifact(result["report_id"], "../.env")


def test_actual_tool_returns_success_and_recoverable_failure(service, monkeypatch):
    monkeypatch.setattr(tool_module, "CsvAnalytics", lambda: service)
    monkeypatch.setattr(tool_module, "get_runtime_thread_id", lambda: "thread")
    events = []
    monkeypatch.setattr(tool_module, "record_event", lambda *a, **kw: events.append(kw))
    uploaded = service.upload(SOURCE.read_bytes(), "orders.csv")
    result = json.loads(tool_module.analyze_sales_csv.invoke({"file_id": uploaded["file_id"]}))
    assert result["ok"] and result["artifacts"]["dashboard"].startswith("/dashboard/api/csv/reports/")
    assert [event["status"] for event in events] == ["in_progress", "completed"]
    assert json.loads(tool_module.analyze_sales_csv.invoke({"file_id": "../.env"}))["ok"] is False
    assert events[-1]["status"] == "error"


def test_csv_task_routes_to_analysis_without_model_call(monkeypatch):
    monkeypatch.setattr("agent.core.task_intent._classify_by_model", lambda *a: pytest.fail("unexpected LLM call"))
    prompt = "CSV销售分析任务\nCSV_FILE_ID=" + "a" * 32 + "\n请调用 analyze_sales_csv 生成看板，无须修改仓库"
    assert classify_task_kind(prompt) == "analysis"


def test_agent_loop_executes_csv_tool_and_receives_artifact_links(service, monkeypatch):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage, ToolMessage

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(tool_module, "CsvAnalytics", lambda: service)
    monkeypatch.setattr(tool_module, "get_runtime_thread_id", lambda: None)
    uploaded = service.upload(SOURCE.read_bytes(), "orders.csv")
    model = Model(responses=[
        AIMessage(content="", tool_calls=[{"id": "sales", "name": "analyze_sales_csv", "args": {"file_id": uploaded["file_id"]}}]),
        AIMessage(content="已根据工具结果完成统计。"),
    ])
    graph = create_agent(model=model, tools=[tool_module.analyze_sales_csv])
    result = graph.invoke({"messages": [{"role": "user", "content": "分析已上传的CSV"}]})
    observed = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert len(observed) == 1
    data = json.loads(observed[0].content)
    assert data["summary"]["net"] == "250.00" and data["artifacts"]["dashboard"].endswith("dashboard.html")
    assert result["messages"][-1].content == "已根据工具结果完成统计。"


def test_category_refund_statistics_use_same_paid_cohort(service):
    rows = sample_rows()
    for row, category in zip(rows, ["数码", "家居", "数码", "无效订单品类"]):
        row["category"] = category
    result = service.analyze(service.upload(csv_bytes(rows), "orders.csv")["file_id"])
    categories = {item["name"]: item for item in result["categories"]}
    assert set(categories) == {"数码", "家居"}
    assert categories["数码"]["paid"] == "150.00"
    assert categories["数码"]["net"] == "100.00"
    assert categories["家居"]["refund_rate"] == "0.250000"
    assert sum(report_decimal(item["paid"]) for item in categories.values()) == report_decimal("350.00")
    assert categories["数码"]["orders"] + categories["家居"]["orders"] == 3
    assert result["summary"]["orders"] == 2
    assert result["refund_analysis"]["amount_rate"] == "0.285714"
    assert result["refund_analysis"]["order_rate"] == "1.000000"
    assert result["refund_analysis"]["refunded_orders"] == 2
    assert result["refund_analysis"]["top_products"][0]["refund"] == "50.00"
    assert any(item["topic"] == "品类" and "150.00" in item["evidence"] for item in result["recommendations"])
    assert any(item["topic"] == "退款" and "50.0%" in item["evidence"] for item in result["recommendations"])
    assert service.artifact(result["report_id"], "categories.csv").is_file()


def report_decimal(value):
    from decimal import Decimal
    return Decimal(value)


def test_refund_and_category_scope_respects_date_filter(service):
    rows = sample_rows()
    for row in rows:
        row["品类"] = "数码"
    result = service.analyze(service.upload(csv_bytes(rows), "orders.csv")["file_id"], start_date="2026-10-03", end_date="2026-10-03")
    assert result["categories"][0]["paid"] == "50.00"
    assert result["refund_analysis"]["amount_rate"] == "1.000000"
    assert result["refund_analysis"]["refunded_orders"] == 1
    # 完全退款且净额为0时，不建议扩大供给。
    assert not any(item["id"] == "category-focus" for item in result["recommendations"])


def test_missing_category_refund_assumptions_and_no_data_are_explicit(service):
    rows = [{"order_id": "O1", "paid_date": "2026-10-01", "paid_amount": "10.00"}]
    uploaded = service.upload(csv_bytes(rows), "orders.csv")
    result = service.analyze(uploaded["file_id"], assume_paid=True, assume_no_refunds=True)
    assert result["categories"] == [] and not result["category_coverage"]["available"]
    assert result["refund_analysis"]["assumed_no_refunds"]
    assert {item["id"] for item in result["recommendations"]} == {"category-missing", "refund-missing"}
    empty = service.analyze(uploaded["file_id"], assume_paid=True, assume_no_refunds=True, start_date="2026-10-02", end_date="2026-10-02")
    assert empty["refund_analysis"]["amount_rate"] is None
    assert empty["refund_analysis"]["order_rate"] is None
    assert empty["recommendations"][0]["id"] == "empty"


def test_latest_report_restore_and_saved_mapping_for_date_updates(service, monkeypatch):
    assert service.latest() is None
    uploaded = service.upload(SOURCE.read_bytes(), "orders.csv")
    result = service.analyze(uploaded["file_id"], field_map={"buyer_id": ""})
    restored = service.latest()
    assert restored["report_id"] == result["report_id"]
    assert restored["daily"] == result["daily"] and restored["recommendations"]
    assert service.get_report(result["report_id"])["summary"] == result["summary"]
    changed = service.analyze(**restored["analysis_options"], start_date="2026-10-01", end_date="2026-10-01")
    assert changed["summary"]["buyers"] is None
    assert changed["summary"]["orders"] == 1
    monkeypatch.setattr(csv_routes, "CsvAnalytics", lambda: service)
    assert csv_routes.latest_report()["report"]["report_id"] == changed["report_id"]
    assert csv_routes.report_detail(changed["report_id"])["summary"]["orders"] == 1


def test_legacy_report_read_has_no_fabricated_category_insights(service):
    result = service.analyze(service.upload(SOURCE.read_bytes(), "orders.csv")["file_id"])
    path = service.artifact(result["report_id"], "report.json")
    old = {key: value for key, value in result.items() if key not in {"schema_version", "categories", "refund_analysis", "recommendations", "analysis_options"}}
    path.write_text(json.dumps(old), encoding="utf-8")
    restored = service.get_report(result["report_id"])
    assert not restored.get("categories")
    assert any("历史报表" in note for note in restored["notes"])
