"""销售CSV统计工具；与HTTP入口复用同一确定性服务。"""
from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from agent.core.csv_analytics import CsvAnalysisError, CsvAnalytics
from agent.core.events import record_event
from agent.tools.runtime_context import get_runtime_thread_id

logger = logging.getLogger("agent.run.csv_analysis")


@tool("analyze_sales_csv", parse_docstring=True)
def analyze_sales_csv(file_id: str, field_map: dict[str, str] | None = None,
                      start_date: str = "", end_date: str = "", currency: str = "CNY",
                      granularity: str = "auto", assume_paid: bool = False,
                      assume_no_refunds: bool = False) -> str:
    """解析用户上传的订单CSV，生成销售统计、HTML看板和可下载的汇总CSV。

    仅创建独立分析产物，不修改仓库，无须克隆仓库。用户须先通过前端CSV统计入口上传文件。
    不支持用API密钥、URL或本机绝对路径代替file_id。成功后将artifacts.dashboard作为Markdown链接返回用户。

    Args:
        file_id: 上传接口返回的32位文件编号，由用户消息提供。
        field_map: 统计字段到原CSV列名的映射，未提供时识别中英文别名。必需order_id、paid_date、paid_amount；支持line_id、refund_amount、buyer_id、product_id、product_name、quantity、status、currency、category、channel。
        start_date: 起始支付日期YYYY-MM-DD，包含当日；空值按数据范围。
        end_date: 结束支付日期YYYY-MM-DD，包含当日；空值按数据范围。
        currency: 三位币种代码，默认CNY，不合并其他币种；时区Asia/Shanghai。
        granularity: auto自动判断；order表示一行一订单；line表示金额为本行商品实收。订单重复且缺明细ID时须明确粒度。
        assume_paid: 缺状态列且用户明确确认全部已支付时才设为true。
        assume_no_refunds: 缺退款列且用户明确确认无退款时才设为true，不能自行猜测。

    Returns:
        JSON结果，ok、summary、notes、artifacts；失败为ok=false和error，不能把失败当作分析完成。
    """
    thread_id = get_runtime_thread_id()
    event_key = "csv_analysis"

    def event(status, detail):
        if thread_id:
            record_event(thread_id, event_key, "解析CSV并生成销售看板", kind="analysis", status=status,
                         detail=json.dumps(detail, ensure_ascii=False))

    event("in_progress", {"tool": "analyze_sales_csv"})
    try:
        result = CsvAnalytics().analyze(file_id, field_map=field_map, start_date=start_date,
                                        end_date=end_date, currency=currency, granularity=granularity,
                                        assume_paid=assume_paid, assume_no_refunds=assume_no_refunds)
        # 图表明细供前端接口读取；工具只回传关键证据，避免大CSV挤满模型上下文。
        result.pop("daily", None)
        result["products"] = result.get("products", [])[:10]
        result["categories"] = result.get("categories", [])[:10]
        event("completed", {"report_id": result["report_id"], "summary": result["summary"]})
    except CsvAnalysisError as exc:
        result = {"ok": False, "error": str(exc), "hint": "核对上传文件编号和字段映射；不要未经确认将缺失数据补为0。"}
        event("error", {"error": str(exc)})
    except OSError:
        logger.warning("CSV分析文件访问失败")
        result = {"ok": False, "error": "无法读取CSV或保存报表，请检查服务存储权限。"}
        event("error", {"error": result["error"]})
    return json.dumps(result, ensure_ascii=False)
