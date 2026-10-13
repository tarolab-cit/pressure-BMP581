"""
BMP581 (DFRobot SEN0665, UART モード) の温度・気圧を定周期で読み込み、画面表示・CSV 保存するプログラム。

気圧から高度も計算する（海面気圧は --sea-level で指定、または --known-altitude で現在地の高度から逆算）。

使い方:
    python pressure_read.py                             # COM7, 10 Hz, 表示のみ
    python pressure_read.py --csv                       # pressure_YYYYmmdd_HHMMSS.csv に保存
    python pressure_read.py --csv data/run1.csv         # ファイル名を指定して保存
    python pressure_read.py --rate 25                   # 25 Hz（センサの ODR も同じ値に設定）
    python pressure_read.py --display-rate 0 --csv      # 画面表示なしで CSV 記録のみ
    python pressure_read.py --duration 10 --csv         # 10 秒間だけ記録して自動終了
    python pressure_read.py --sea-level 1010.5          # 高度計算の海面気圧 [hPa] を指定
    python pressure_read.py --known-altitude 30         # 現在地の高度 30 m として海面気圧を逆算
    python pressure_read.py --osr-p 16 --iir-p 7        # 気圧の OSR 16 倍・IIR 係数 7 でノイズ低減
    python pressure_read.py --plot                      # 高度・気圧をリアルタイムにグラフ表示（matplotlib）
    python pressure_read.py --plot --plot-window 60     # グラフの時間幅を 60 秒に

Ctrl+C で終了します。--plot のときはグラフウィンドウが残るので、閉じると終了します。
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime

from bmp581_uart import BMP581UART, BMP581Error, PressureData, OSR_TABLE, IIR_TABLE


def format_line(d: PressureData) -> str:
    """1 行表示（--scroll 指定時・パイプ／リダイレクト時用）"""
    return f"t={d.t:8.3f}s | temp={d.temp:7.3f} degC | press={d.press:9.3f} hPa | alt={d.alt:8.2f} m"


BLOCK_LINES = 4


def _pad(label: str, width: int) -> str:
    """全角文字を幅 2 として label を width 桁に右パディング"""
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in label)
    return label + " " * max(0, width - w)


def format_block(d: PressureData, n_ok: int, n_err: int, sea_level_hpa: float) -> str:
    """端末用の複数行ブロック表示（BLOCK_LINES 行）"""
    def row(key: str, jp: str, unit: str, values: str) -> str:
        return f"{key:<5} {_pad(jp, 8)} [{unit:<4}]  {values}"

    lines = [
        f"t = {d.t:9.3f} s   samples = {n_ok:7d}   errors = {n_err:4d}",
        row("temp", "温度", "degC", f"{d.temp:9.3f}"),
        row("press", "気圧", "hPa", f"{d.press:9.3f}   ({d.press * 100:.2f} Pa)"),
        row("alt", "高度", "m", f"{d.alt:9.2f}   (海面気圧 {sea_level_hpa:.2f} hPa 基準)"),
    ]
    assert len(lines) == BLOCK_LINES
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="BMP581 UART 定周期読み出し・表示・CSV 保存")
    ap.add_argument("--port", default="COM7", help="シリアルポート (default: COM7)")
    ap.add_argument("--baud", type=int, default=9600, help="ボーレート (default: 9600)")
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x47,
                    help="Modbus スレーブ ID (default: 0x47。DIP スイッチ 2 で 0x46)")
    ap.add_argument("--rate", type=float, default=10.0,
                    help="サンプリング周期 [Hz] (default: 10)。センサの ODR も同じ値（最も近い設定値）にする")
    ap.add_argument("--csv", nargs="?", const="", default=None, metavar="FILE",
                    help="CSV 保存。ファイル名省略時は pressure_YYYYmmdd_HHMMSS.csv")
    ap.add_argument("--display-rate", type=float, default=5.0,
                    help="画面表示の更新頻度 [Hz] (default: 5)。0 で表示なし")
    ap.add_argument("--scroll", action="store_true",
                    help="表示を同じ位置で更新せず 1 行ずつスクロール出力する")
    ap.add_argument("--duration", type=float, default=None, help="記録時間 [s]。省略時は Ctrl+C まで")
    ap.add_argument("--sea-level", type=float, default=1013.25,
                    help="高度計算に使う海面気圧 [hPa] (default: 1013.25)")
    ap.add_argument("--known-altitude", type=float, default=None, metavar="M",
                    help="現在地の高度 [m]。指定すると起動時の気圧から海面気圧を逆算して基準にする")
    ap.add_argument("--osr-t", type=int, default=1, choices=sorted(OSR_TABLE), help="温度のオーバーサンプリング倍率 (default: 1)")
    ap.add_argument("--osr-p", type=int, default=1, choices=sorted(OSR_TABLE), help="気圧のオーバーサンプリング倍率 (default: 1)")
    ap.add_argument("--iir-t", type=int, default=0, choices=sorted(IIR_TABLE), help="温度の IIR フィルタ係数 (default: 0=なし)")
    ap.add_argument("--iir-p", type=int, default=0, choices=sorted(IIR_TABLE), help="気圧の IIR フィルタ係数 (default: 0=なし)")
    ap.add_argument("--no-reset", action="store_true", help="起動時にソフトリセットしない")
    ap.add_argument("--plot", action="store_true", help="高度・気圧をリアルタイムにグラフ表示する（matplotlib が必要）")
    ap.add_argument("--plot-window", type=float, default=30.0, metavar="SEC",
                    help="グラフに表示する時間幅 [s] (default: 30)")
    args = ap.parse_args()

    if args.plot:
        try:
            import matplotlib  # noqa: F401  存在確認のみ（描画は子プロセスで行う）
        except ImportError:
            print("--plot には matplotlib が必要です: pip install matplotlib", file=sys.stderr)
            return 1

    period = 1.0 / args.rate
    display_every = max(1, int(round(args.rate / args.display_rate))) if args.display_rate > 0 else 0

    try:
        bmp = BMP581UART(args.port, args.baud, args.addr)
    except Exception as e:
        print(f"シリアルポート {args.port} を開けません: {e}", file=sys.stderr)
        return 1

    with bmp:
        try:
            bmp.begin(reset=not args.no_reset, odr_hz=args.rate,
                      osr_t=args.osr_t, osr_p=args.osr_p, iir_t=args.iir_t, iir_p=args.iir_p)
        except BMP581Error as e:
            print(f"BMP581 初期化失敗: {e}", file=sys.stderr)
            print("DIP スイッチ（1: UART 側, 2: アドレス）、ボーレート、TX/RX 配線を確認してください。", file=sys.stderr)
            return 1

        odr_hz = bmp.set_odr(args.rate)
        print(f"BMP581 接続OK  port={args.port}  baud={args.baud}  addr=0x{args.addr:02X}  "
              f"rate={args.rate} Hz (ODR={odr_hz} Hz)  OSR t/p={args.osr_t}/{args.osr_p}  IIR t/p={args.iir_t}/{args.iir_p}")
        if not bmp.odr_is_valid():
            print("警告: この OSR では ODR が高すぎます（測定周期が ODR より長くなります）。--osr-p を下げるか --rate を下げてください。")

        if args.known_altitude is not None:
            time.sleep(0.2)
            sea_level_hpa = bmp.calibrate_altitude(args.known_altitude)
            print(f"海面気圧を逆算: {sea_level_hpa:.2f} hPa（高度 {args.known_altitude} m 基準）")
        else:
            bmp.set_sea_level(args.sea_level)
            sea_level_hpa = args.sea_level

        # CSV 準備
        csv_file = None
        writer = None
        if args.csv is not None:
            path = args.csv or datetime.now().strftime("pressure_%Y%m%d_%H%M%S.csv")
            csv_file = open(path, "w", newline="", encoding="utf-8")
            writer = csv.writer(csv_file)
            writer.writerow(["timestamp"] + PressureData.field_names())
            print(f"CSV 保存先: {path}")

        # グラフ（別プロセス）。サンプリングループは Queue に入れるだけで描画を待たない
        plot_proc = None
        plot_q = None
        if args.plot:
            from multiprocessing import Process, Queue
            from live_plot import run_plot
            plot_q = Queue()
            plot_proc = Process(target=run_plot, args=(plot_q, args.plot_window, sea_level_hpa, "BMP581"), daemon=True)
            plot_proc.start()
            print(f"グラフ表示: 直近 {args.plot_window:g} 秒（ウィンドウを閉じても記録は続きます）")

        print("Ctrl+C で終了\n")

        # 端末に直接出力しているときは複数行ブロックを同じ位置で更新、それ以外は 1 行ずつ出力
        block_mode = bool(display_every) and not args.scroll and sys.stdout.isatty()
        if block_mode:
            os.system("")  # Windows コンソールで ANSI エスケープを有効化
            print("\n" * (BLOCK_LINES - 1))

        n_ok = n_err = 0
        t_start = time.perf_counter()
        next_t = t_start
        try:
            while True:
                next_t += period
                try:
                    d = bmp.read_all()
                    n_ok += 1
                    if writer:
                        writer.writerow([f"{time.time():.6f}", f"{d.t:.4f}", f"{d.temp:.4f}", f"{d.press:.4f}", f"{d.alt:.3f}"])
                    if plot_q is not None and plot_proc.is_alive():
                        plot_q.put((d.t, d.alt, d.press))
                    if display_every and n_ok % display_every == 0:
                        if block_mode:
                            # カーソルを BLOCK_LINES 行上げてブロックを上書き（ESC[K で各行末まで消去）
                            body = format_block(d, n_ok, n_err, sea_level_hpa).replace("\n", "\x1b[K\n")
                            sys.stdout.write(f"\x1b[{BLOCK_LINES}A\r{body}\x1b[K\n")
                            sys.stdout.flush()
                        else:
                            print(format_line(d))
                except BMP581Error as e:
                    n_err += 1
                    print(f"読み出しエラー: {e}", file=sys.stderr)

                now = time.perf_counter()
                if args.duration is not None and now - t_start >= args.duration:
                    break
                # 定周期化: 次の予定時刻まで待つ。遅延が溜まった場合は追いつくためスキップ
                if next_t - now > 0:
                    time.sleep(next_t - now)
                elif now - next_t > period:
                    next_t = now
        except KeyboardInterrupt:
            pass
        finally:
            elapsed = time.perf_counter() - t_start
            if csv_file:
                csv_file.close()
            print(f"\n終了: {elapsed:.2f} s, 取得 {n_ok} サンプル ({n_ok / elapsed if elapsed else 0:.1f} Hz), エラー {n_err} 回")
            if plot_proc is not None and plot_proc.is_alive():
                plot_q.put(None)   # 記録終了を通知（グラフは残る）
                print("グラフウィンドウを閉じると終了します")
                plot_proc.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
