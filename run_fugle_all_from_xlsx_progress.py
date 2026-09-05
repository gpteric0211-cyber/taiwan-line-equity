# -*- coding: utf-8 -*-
r"""
Fugle 全股票 intraday 補充資料批次更新器。

放置位置：
  <repo-root>\run_fugle_all_from_xlsx_progress.py

基本執行：
  cd /d <repo-root>
  python run_fugle_all_from_xlsx_progress.py

從第 2 批繼續：
  python run_fugle_all_from_xlsx_progress.py --start-batch 2

只跑第 20 到第 25 批：
  python run_fugle_all_from_xlsx_progress.py --start-batch 20 --stop-batch 25

功能：
- 從 XLSX 讀取 4 位數股票代號
- 分批呼叫 scripts/update_fugle_intraday_supplemental.py
- 顯示中文進度、完成百分比、剩餘股票、已執行時間、預估剩餘時間
- 失敗批次自動重試
- 失敗批次最後列出
- --dry-run 只列出批次，不呼叫更新腳本、不抓 Fugle API、不寫 DB
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Iterable, List

DEFAULT_XLSX = "台股上市公司精準分類表_全1974檔_全量分類修正版.xlsx"
DEFAULT_DB = Path("review_src") / "data" / "taiwan50.db"
DEFAULT_CODES_PER_BATCH = 30
# Per-request pacing is enforced centrally by RollingRequestLimiter in the
# child updater.  A second fixed delay would under-use the same safe budget.
DEFAULT_SLEEP_SECONDS = 0.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_WAIT_SECONDS = 60
PARTIAL_RETRYABLE = 4
SOURCE_DELAYED_RETRYABLE = 5
RETRYABLE_EXIT_CODES = {PARTIAL_RETRYABLE, SOURCE_DELAYED_RETRYABLE}
FIXED_EXIT_CODES = {0, 1, 2, 3, 4, 5}


def fmt_seconds(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "計算中"
    seconds = int(seconds)
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def load_codes_from_xlsx(path: Path) -> List[str]:
    try:
        from openpyxl import load_workbook
    except Exception as exc:
        raise RuntimeError("XLSX mode requires openpyxl") from exc
    if not path.exists():
        raise FileNotFoundError(f"找不到 Excel 檔：{path}")

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active

    codes: List[str] = []
    seen = set()

    # 從第二列開始讀，避免 header 若剛好是 4 位數字時被誤判。
    for row in ws.iter_rows(min_row=2, values_only=True):
        # 預期股票代號在 A 欄；掃前 5 欄是為了容錯。
        for cell in row[:5]:
            if cell is None:
                continue
            s = str(cell).strip()
            if re.fullmatch(r"\d{4}", s):
                if s not in seen:
                    seen.add(s)
                    codes.append(s)
                break

    return codes


def load_codes_from_database(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"找不到資料庫：{path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        stock_master = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_master'"
        ).fetchone()
        if stock_master:
            rows = conn.execute(
                """
                SELECT code
                FROM stock_master
                WHERE is_active=1 AND security_type='stock'
                ORDER BY market,code
                """
            ).fetchall()
        else:
            rows = []
        if not rows:
            rows = conn.execute(
                "SELECT code FROM stock_industry_profile ORDER BY market,code"
            ).fetchall()
        return [str(row[0]) for row in rows if re.fullmatch(r"\d{4}", str(row[0] or ""))]
    finally:
        conn.close()


def chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def find_python(repo_root: Path) -> str:
    for venv_py in (
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / "review_src" / ".venv" / "Scripts" / "python.exe",
    ):
        if venv_py.exists():
            return str(venv_py)
    return sys.executable or "python"


def aggregate_exit_code(exit_codes: Iterable[int]) -> int:
    """Aggregate child outcomes without turning a batch count into an exit code."""

    normalized = [int(code) for code in exit_codes if int(code) != 0]
    if not normalized:
        return 0
    if any(code not in FIXED_EXIT_CODES for code in normalized):
        return 2
    for code in (3, 2, 1, SOURCE_DELAYED_RETRYABLE, PARTIAL_RETRYABLE):
        if code in normalized:
            return code
    return 2


def show_progress(
    *,
    completed_codes: int,
    total_codes: int,
    completed_calls: int,
    total_calls: int,
    processed_this_run: int,
    failed_batches: List[str],
    start_time: float,
) -> None:
    elapsed = time.time() - start_time
    remaining_codes = max(0, total_codes - completed_codes)
    pct = (completed_codes / total_codes * 100.0) if total_codes else 0.0

    # ETA 用「本次實際處理數」估算，避免 resume 模式把 skipped 批次算進速度。
    eta = None if processed_this_run <= 0 else elapsed * remaining_codes / processed_this_run

    print("")
    print("------------------------------------------------------------")
    print(f"[進度] 已處理 {completed_codes}/{total_codes} 檔股票（{pct:.2f}%）")
    print(f"[剩餘] 尚未處理 {remaining_codes} 檔股票")
    print(f"[API] 約已呼叫 {completed_calls}/{total_calls} 次 API")
    print(f"[時間] 已執行 {fmt_seconds(elapsed)}；預估剩餘 {fmt_seconds(eta)}")
    print(f"[未完成] 目前未完成批次數：{len(failed_batches)}")
    if failed_batches:
        print(f"[未完成批次] {', '.join(failed_batches)}")
    print("------------------------------------------------------------")
    print("")


def run_batch(
    *,
    repo_root: Path,
    python_exe: str,
    batch_id: int,
    total_batches: int,
    codes: List[str],
    sleep_seconds: float,
    current_report_path: Path,
    max_retries: int,
    retry_wait_seconds: int,
    capture_phase: str,
    window_start: str,
    window_end: str,
) -> int:
    joined_codes = ",".join(codes)
    max_attempts = max_retries + 1

    print("")
    print("=" * 72)
    print(f"[批次] 第 {batch_id:03d} 批 / 共 {total_batches:03d} 批")
    print(f"[股票] {joined_codes}")
    print(f"[本批數量] {len(codes)} 檔股票，約 {len(codes) * 2} 次 API")
    print("=" * 72)

    for attempt in range(1, max_attempts + 1):
        report_path = current_report_path
        cmd = [
            python_exe,
            str(repo_root / "scripts" / "update_fugle_intraday_supplemental.py"),
            "--codes",
            joined_codes,
            "--write",
            "--capture-phase",
            capture_phase,
            "--window-start",
            window_start,
            "--window-end",
            window_end,
            "--sleep-seconds",
            str(sleep_seconds),
            "--output",
            str(report_path),
        ]

        print("")
        print(f"[嘗試] 第 {batch_id:03d} 批，第 {attempt}/{max_attempts} 次")
        print(f"[報告] {report_path}")

        result = subprocess.run(cmd, cwd=str(repo_root))
        rc = result.returncode

        if rc == 0:
            report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
            if "- 是否寫 DB：是" in report_text:
                print(f"[成功] 第 {batch_id:03d} 批已寫入資料，成功於第 {attempt} 次嘗試。")
            else:
                print(f"[略過] 第 {batch_id:03d} 批正常結束，但沒有資料寫入 DB；請查看批次報告原因。")
            return 0

        if rc == PARTIAL_RETRYABLE:
            print(f"[部分完成] 第 {batch_id:03d} 批仍有真正未完成股票，exit code = 4")
        elif rc == SOURCE_DELAYED_RETRYABLE:
            print(f"[來源延遲] 第 {batch_id:03d} 批等待上游資料，exit code = 5")
        else:
            print(f"[失敗] 第 {batch_id:03d} 批為不可重試錯誤，exit code = {rc}")
            return rc if rc in FIXED_EXIT_CODES else 2

        if attempt < max_attempts:
            print(f"[重試] 等待 {retry_wait_seconds} 秒後重抓第 {batch_id:03d} 批...")
            time.sleep(retry_wait_seconds)

    print(
        f"[未完成] 第 {batch_id:03d} 批嘗試 {max_attempts} 次後仍為可重試狀態，"
        f"保留 exit code = {rc}。"
    )
    return rc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fugle 全股票 XLSX 批次更新器")
    parser.add_argument("--codes", help="Optional comma-separated four-digit stock codes; overrides the universe source")
    parser.add_argument("--universe-source", choices=["database", "xlsx"], default="database", help="股票清單來源；預設讀官方同步後的 stock_master")
    parser.add_argument("--database", default=str(DEFAULT_DB), help="相對於專案根目錄的 SQLite 路徑")
    parser.add_argument("--xlsx", default=DEFAULT_XLSX, help="Excel 檔名或完整路徑")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_CODES_PER_BATCH, help="每批股票數")
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS, help="每次 API request 間隔秒數")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="每批失敗後最多重試次數")
    parser.add_argument("--retry-wait-seconds", type=int, default=DEFAULT_RETRY_WAIT_SECONDS, help="每次重試前等待秒數")
    parser.add_argument("--start-batch", type=int, default=1, help="從第幾批開始，預設 1")
    parser.add_argument("--stop-batch", type=int, default=-1, help="跑到第幾批為止，-1 代表跑到最後")
    parser.add_argument("--dry-run", action="store_true", help="只列出批次，不呼叫更新腳本、不抓 Fugle API、不寫 DB；這不是子程式 dry-run")
    parser.add_argument("--capture-phase", choices=["post_close", "latest_completed"], default="latest_completed")
    parser.add_argument("--window-start", default="13:31")
    parser.add_argument("--window-end", default="23:59")
    parser.add_argument(
        "--skip-final-verify",
        action="store_true",
        help="Skip the global market-foundation verifier when a parent pipeline will run it once at the end.",
    )
    # 不在 runner 加 --date：避免誤導為 Fugle 可補任意歷史日期完整逐筆。
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    repo_root = Path(__file__).resolve().parent
    xlsx_path = Path(args.xlsx)
    if not xlsx_path.is_absolute():
        xlsx_path = repo_root / xlsx_path

    database_path = Path(args.database)
    if not database_path.is_absolute():
        database_path = repo_root / database_path
    explicit_codes = list(dict.fromkeys(re.findall(r"\b\d{4}\b", str(args.codes or ""))))
    codes = explicit_codes or (
        load_codes_from_database(database_path)
        if args.universe_source == "database"
        else load_codes_from_xlsx(xlsx_path)
    )
    if not codes:
        print("[錯誤] 股票母體中沒有找到 4 位數股票代號。")
        return 1

    total_codes = len(codes)
    total_calls = total_codes * 2
    batches = list(chunks(codes, args.batch_size))
    total_batches = len(batches)
    stop_batch = args.stop_batch if args.stop_batch > 0 else total_batches

    if args.start_batch < 1:
        print("[錯誤] --start-batch 必須 >= 1")
        return 1
    if stop_batch < args.start_batch:
        print("[錯誤] --stop-batch 不可小於 --start-batch")
        return 1
    if args.start_batch > total_batches:
        print(f"[錯誤] --start-batch 超過總批次數：{total_batches}")
        return 1
    if stop_batch > total_batches:
        stop_batch = total_batches

    report_dir = repo_root / "logs" / "fugle_intraday_update_json"
    current_report_path = report_dir / "FUGLE_INTRADAY_UPDATE_CURRENT_BATCH.md"
    final_report_path = report_dir / "FUGLE_ALL_XLSX_FINAL_SUMMARY.md"
    python_exe = find_python(repo_root)

    print("=" * 72)
    print("Fugle 全股票 intraday 補充資料批次更新器")
    print("除非使用 --dry-run，否則這是正式 DB 寫入。")
    print(f"專案資料夾：{repo_root}")
    code_source = "explicit_codes" if explicit_codes else args.universe_source
    print(f"股票清單來源：{code_source}")
    print(f"資料庫：{database_path}" if args.universe_source == "database" else f"Excel 檔案：{xlsx_path}")
    print(f"Python：{python_exe}")
    print(f"總股票數：{total_codes}")
    print(f"總批次數：{total_batches}")
    print("Excel 讀取方式：跳過第一列 header，讀取 4 位數股票代號")
    print(f"每批股票數：{args.batch_size}")
    print(f"預估 API 呼叫總數：{total_calls}")
    print(f"每次 API 間隔：{args.sleep_seconds} 秒")
    print(f"每批失敗最多重試：{args.max_retries} 次")
    print(f"重試前等待：{args.retry_wait_seconds} 秒")
    print(f"抓取階段：{args.capture_phase}；允許時窗 {args.window_start}-{args.window_end}")
    print(f"最終結果紀錄：{final_report_path}")
    print("批次過程不再保留 66 份報告，只覆蓋同一份暫存報告，結束後刪除暫存。")
    print("=" * 72)

    # 建立 logs/fugle_intraday_update_json 目錄；不建立 batch_reports。
    # dry-run 也建立此目錄，避免其他工具假設資料夾存在時找不到。
    report_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print("")
        print("[Dry-run] 這次只列出預計批次。")
        print("[Dry-run] 不會呼叫 scripts\\update_fugle_intraday_supplemental.py。")
        print("[Dry-run] 不會抓 Fugle API。")
        print("[Dry-run] 不會寫入 DB。")
        print("[Dry-run] 這不是子程式 update_fugle_intraday_supplemental.py 的 dry-run。")
        print("")
        print("[Dry-run] 預計執行批次如下：")
        for idx, batch in enumerate(batches, start=1):
            if idx < args.start_batch or idx > stop_batch:
                continue
            print(f"第 {idx:03d}/{total_batches:03d} 批：{','.join(batch)}")
        return 0

    start_time = time.time()
    skipped_codes = 0
    completed_codes = 0
    completed_calls = 0
    processed_this_run = 0
    failed_batches: List[str] = []
    batch_results: List[dict] = []

    # resume 模式：跳過的批次只算在 whole-file progress，不算本次處理速度。
    if args.start_batch > 1:
        skipped_codes = sum(len(b) for b in batches[: args.start_batch - 1])
        completed_codes = skipped_codes
        completed_calls = completed_codes * 2

    show_progress(
        completed_codes=completed_codes,
        total_codes=total_codes,
        completed_calls=completed_calls,
        total_calls=total_calls,
        processed_this_run=processed_this_run,
        failed_batches=failed_batches,
        start_time=start_time,
    )

    for idx, batch in enumerate(batches, start=1):
        if idx < args.start_batch or idx > stop_batch:
            continue

        print(f"[目前] 準備執行第 {idx:03d}/{total_batches:03d} 批")
        show_progress(
            completed_codes=completed_codes,
            total_codes=total_codes,
            completed_calls=completed_calls,
            total_calls=total_calls,
            processed_this_run=processed_this_run,
            failed_batches=failed_batches,
            start_time=start_time,
        )

        batch_exit_code = run_batch(
            repo_root=repo_root,
            python_exe=python_exe,
            batch_id=idx,
            total_batches=total_batches,
            codes=batch,
            sleep_seconds=args.sleep_seconds,
            current_report_path=current_report_path,
            max_retries=args.max_retries,
            retry_wait_seconds=args.retry_wait_seconds,
            capture_phase=args.capture_phase,
            window_start=args.window_start,
            window_end=args.window_end,
        )

        batch_results.append({
            "batch": f"{idx:03d}",
            "codes": ",".join(batch),
            "count": len(batch),
            "ok": batch_exit_code == 0,
            "exit_code": batch_exit_code,
        })

        if batch_exit_code != 0:
            failed_batches.append(f"{idx:03d}")

        processed_this_run += len(batch)
        completed_codes += len(batch)
        completed_calls = completed_codes * 2

        print(f"[完成] 第 {idx:03d}/{total_batches:03d} 批結束")
        show_progress(
            completed_codes=completed_codes,
            total_codes=total_codes,
            completed_calls=completed_calls,
            total_calls=total_calls,
            processed_this_run=processed_this_run,
            failed_batches=failed_batches,
            start_time=start_time,
        )

    selected_batches = [
        batch for idx, batch in enumerate(batches, start=1)
        if idx >= args.start_batch and idx <= stop_batch
    ]
    selected_total_codes = sum(len(batch) for batch in selected_batches)

    print("=" * 72)
    print("批次更新完成。")
    print(f"整份母體處理進度：{completed_codes}/{total_codes} 檔股票")
    print(f"本次開始前已跳過：{skipped_codes} 檔股票")
    print(f"本次選定要跑：{selected_total_codes} 檔股票")
    print(f"本次實際處理：{processed_this_run}/{selected_total_codes} 檔股票")
    print(f"未完成批次數：{len(failed_batches)}")
    if failed_batches:
        print(f"未完成批次 ID：{', '.join(failed_batches)}")
        print("可用以下方式重跑單一批次，例如：")
        print("  python run_fugle_all_from_xlsx_progress.py --start-batch 12 --stop-batch 12")
    print("=" * 72)

    # 寫入單一最終結果紀錄；每次執行都覆蓋。
    elapsed_total = time.time() - start_time
    lines = [
        "# Fugle 全股票 XLSX 批次更新最終結果",
        "",
        f"- 股票清單來源：{code_source}",
        f"- 資料庫：{database_path}" if args.universe_source == "database" else f"- Excel：{xlsx_path}",
        f"- 總股票數：{total_codes}",
        f"- 總批次數：{total_batches}",
        f"- 本次起始批次：{args.start_batch}",
        f"- 本次結束批次：{stop_batch}",
        f"- 本次選定股票數：{selected_total_codes}",
        f"- 本次實際處理股票數：{processed_this_run}",
        f"- 整份母體處理進度：{completed_codes}/{total_codes}",
        f"- 未完成批次數：{len(failed_batches)}",
        f"- 未完成批次：{', '.join(failed_batches) if failed_batches else '無'}",
        f"- 已執行時間：{fmt_seconds(elapsed_total)}",
        f"- sleep seconds：{args.sleep_seconds}",
        f"- max retries：{args.max_retries}",
        "",
        "## 批次結果",
        "",
    ]
    for item in batch_results:
        exit_code = int(item.get("exit_code") or 0)
        status = (
            "成功"
            if exit_code == 0
            else "部分完成、可重試"
            if exit_code == PARTIAL_RETRYABLE
            else "來源延遲、可重試"
            if exit_code == SOURCE_DELAYED_RETRYABLE
            else "不可重試失敗"
        )
        lines.append(
            f"- Batch {item['batch']}：{status}（exit {exit_code}），"
            f"{item['count']} 檔，{item['codes']}"
        )
    final_report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[最終紀錄] 已寫入：{final_report_path}")

    # 不保留每批 runtime 報告，只保留單一最終 summary。
    try:
        if current_report_path.exists():
            current_report_path.unlink()
            print(f"[清理] 已刪除暫存批次報告：{current_report_path}")
    except OSError as exc:
        print(f"[警告] 暫存批次報告刪除失敗：{exc}")

    if not args.skip_final_verify:
        print("")
        print("正在執行中文完整度檢查...")
        verify_cmd = [
            python_exe,
            str(repo_root / "scripts" / "verify_market_foundation_update.py"),
            "--min-count",
            "1000",
            "--zh",
            "--details",
        ]
        verify = subprocess.run(verify_cmd, cwd=str(repo_root))
        if verify.returncode != 0:
            print("[警告] 中文完整度檢查失敗，請手動執行：")
            print("  python scripts\\verify_market_foundation_update.py --min-count 1000 --zh --details")

    return aggregate_exit_code(item.get("exit_code", 0) for item in batch_results)


if __name__ == "__main__":
    from core.tls_config import configure_tls
    configure_tls()
    raise SystemExit(main())
