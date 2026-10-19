"""Check the deployed release, history, and CSV sales dashboard using existing site login."""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    access = dict(line.split(": ", 1) for line in (ROOT / "data/linux-deployment/access.txt").read_text(encoding="utf-8").splitlines() if ": " in line)
    base = access["URL"].rstrip("/")
    with requests.Session() as session:
        session.trust_env = False
        assert session.get(base, timeout=15).status_code == 401
        session.auth = (access["Username"], access["Password"])
        page = session.get(base, timeout=20)
        page.raise_for_status()
        current_js = re.search(r'src="([^"]+\.js)"', (ROOT / "ui/dist/index.html").read_text(encoding="utf-8"))[1]
        assert current_js in page.text, "Site did not serve the new frontend index"
        script = session.get(base + current_js, timeout=20)
        script.raise_for_status()
        assert script.content == (ROOT / "ui/dist" / current_js.lstrip("/")).read_bytes()
        print("PASS: authentication and exact frontend build", flush=True)
        health = session.get(base + "/health", timeout=20)
        health.raise_for_status()
        assert health.json()["ok"] and health.json()["checkpoint_backend"] == "mongodb"
        threads = session.get(base + "/dashboard/api/threads", timeout=20)
        threads.raise_for_status()
        counts = []
        for thread in threads.json():
            history = session.get(base + "/dashboard/api/threads/" + thread["id"], timeout=35)
            history.raise_for_status()
            counts.append(len(history.json().get("messages", [])))
        print(f"PASS: health and existing histories ({counts})", flush=True)
        # Explicit demo rows keep the new dashboard visibly marked as sample data.
        csv_file = ROOT / "artifacts/order-statistics-demo/orders-demo.csv"
        uploaded = session.post(base + "/dashboard/api/csv/uploads", params={"filename": "orders-demo.csv"},
                                data=csv_file.read_bytes(), headers={"Content-Type": "application/octet-stream"}, timeout=30)
        uploaded.raise_for_status()
        generated = session.post(base + "/dashboard/api/csv/reports", json={"file_id": uploaded.json()["file_id"]}, timeout=60)
        generated.raise_for_status()
        report = generated.json()
        assert report["summary"]["net"] == "7960.85" and report["summary"]["orders"] == 28
        assert report["data_label"] == "演示数据" and len(report["categories"]) == 4
        assert report["refund_analysis"]["refunded_orders"] == 6
        assert any(item["topic"] == "退款" for item in report["recommendations"])
        assert any(item["topic"] == "品类" for item in report["recommendations"])
        for name, endpoint in report["artifacts"].items():
            response = session.get(base + endpoint, timeout=20)
            response.raise_for_status()
            if name == "dashboard":
                assert "sandbox" in response.headers["Content-Security-Policy"]
        latest = session.get(base + "/dashboard/api/csv/latest", timeout=20)
        latest.raise_for_status()
        assert latest.json()["report"]["report_id"] == report["report_id"]
        output = ROOT / "data/linux-update/verification.json"
        output.write_text(json.dumps({"url": base, "history_message_counts": counts, "report_id": report["report_id"],
                                      "summary": report["summary"], "categories": report["categories"],
                                      "recommendations": report["recommendations"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: CSV upload, sales dashboard, category/refund advice and all downloads", flush=True)


if __name__ == "__main__":
    main()
