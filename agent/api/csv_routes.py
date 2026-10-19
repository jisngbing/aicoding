"""CSV上传、确定性分析与报表下载入口。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent.core.csv_analytics import MAX_BYTES, CsvAnalysisError, CsvAnalytics

csv_router = APIRouter(prefix="/dashboard/api/csv", tags=["csv"])


class AnalyzeRequest(BaseModel):
    file_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    field_map: dict[str, str] | None = None
    start_date: str = Field(default="", max_length=10)
    end_date: str = Field(default="", max_length=10)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    granularity: str = Field(default="auto", pattern=r"^(auto|order|line)$")
    assume_paid: bool = False
    assume_no_refunds: bool = False


@csv_router.post("/uploads")
async def upload_csv(request: Request, filename: str = "orders.csv"):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "CSV不能超过10MB")
        data.extend(chunk)
    try:
        return await run_in_threadpool(CsvAnalytics().upload, bytes(data), filename)
    except CsvAnalysisError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except OSError:
        raise HTTPException(503, "上传文件保存失败，请检查存储权限") from None


@csv_router.post("/reports")
async def analyze_csv(body: AnalyzeRequest):
    options = body.model_dump()
    file_id = options.pop("file_id")
    try:
        return await run_in_threadpool(CsvAnalytics().analyze, file_id, **options)
    except CsvAnalysisError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except OSError:
        raise HTTPException(503, "读取文件或生成报表失败，请检查存储权限") from None


@csv_router.get("/reports/{report_id}/{filename}")
def report_file(report_id: str, filename: str):
    try:
        path = CsvAnalytics().artifact(report_id, filename)
    except CsvAnalysisError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
               "Referrer-Policy": "no-referrer"}
    if filename == "dashboard.html":
        # 上传内容只能作为转义后的数据展示，页面禁止脚本、外连和表单提交。
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
        return FileResponse(path, media_type="text/html", headers=headers)
    return FileResponse(path, filename=filename, headers=headers)


@csv_router.get("/latest")
def latest_report():
    try:
        return {"report": CsvAnalytics().latest()}
    except OSError:
        raise HTTPException(503, "读取统计报表失败，请稍后重试") from None


@csv_router.get("/reports/{report_id}")
def report_detail(report_id: str):
    try:
        return CsvAnalytics().get_report(report_id)
    except CsvAnalysisError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except OSError:
        raise HTTPException(503, "读取统计报表失败，请稍后重试") from None
