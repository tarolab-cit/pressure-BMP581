"""
高度・気圧のリアルタイムグラフ（pressure_read.py --plot 用）。

サンプリングの定周期を乱さないよう、グラフは別プロセスで描画する。
親プロセスは multiprocessing.Queue に (t, alt, press) を入れるだけで、
このモジュールの run_plot() が子プロセス側で一定間隔（PLOT_INTERVAL_MS）にまとめて取り出して描画する。
Queue に None を入れると「記録終了」とみなし、ウィンドウはそのまま残して更新だけ止める。

単体テスト用:
    python live_plot.py        # 疑似データを流してグラフを表示
"""

from __future__ import annotations

import signal
from collections import deque
from multiprocessing import Queue

PLOT_INTERVAL_MS = 100   # 描画更新間隔 [ms]（10 fps）

# 色（dataviz 参照パレット: 系列 1 = 青, 系列 2 = 橙。文字・軸は中立色）
COLOR_ALT = "#2a78d6"
COLOR_PRESS = "#eb6834"
COLOR_TEXT = "#0b0b0b"
COLOR_TEXT_2 = "#52514e"
COLOR_GRID = "#e4e3df"
COLOR_SURFACE = "#fcfcfb"

MIN_SPAN_ALT = 2.0      # 高度軸の最小レンジ [m]（ノイズだけのとき拡大しすぎない）
MIN_SPAN_PRESS = 0.3    # 気圧軸の最小レンジ [hPa]


def _autoscale(ax, values: deque, min_span: float) -> None:
    """表示中データの範囲に上下 15% の余白を付けて y 軸を合わせる。範囲が min_span 未満なら中心を保って広げる。"""
    lo, hi = min(values), max(values)
    span = max(hi - lo, min_span)
    mid = (lo + hi) / 2
    ax.set_ylim(mid - span * 0.65, mid + span * 0.65)


def run_plot(q: Queue, window_s: float = 30.0, sea_level_hpa: float = 1013.25,
             title: str = "BMP581", auto_close: bool = False) -> None:
    """子プロセスのエントリ。Queue から (t, alt, press) を受け取り、直近 window_s 秒を描画し続ける。"""
    # Ctrl+C は親プロセスが処理する（子はコンソールの Ctrl+C を無視し、親からの None で終了状態になる）
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.rcParams.update({
        "figure.facecolor": COLOR_SURFACE, "axes.facecolor": COLOR_SURFACE,
        "text.color": COLOR_TEXT, "axes.labelcolor": COLOR_TEXT_2,
        "xtick.color": COLOR_TEXT_2, "ytick.color": COLOR_TEXT_2,
        "axes.edgecolor": COLOR_GRID, "grid.color": COLOR_GRID,
        "axes.spines.top": False, "axes.spines.right": False,
        "font.size": 10,
    })

    ts: deque[float] = deque()
    alts: deque[float] = deque()
    press: deque[float] = deque()

    fig, (ax_alt, ax_p) = plt.subplots(2, 1, sharex=True, figsize=(9, 6))
    fig.canvas.manager.set_window_title(f"{title} live plot")
    fig.suptitle(f"{title}   altitude / pressure   (sea level {sea_level_hpa:.2f} hPa)", color=COLOR_TEXT)

    (line_alt,) = ax_alt.plot([], [], color=COLOR_ALT, linewidth=1.6)
    (line_p,) = ax_p.plot([], [], color=COLOR_PRESS, linewidth=1.6)
    ax_alt.set_ylabel("Altitude [m]")
    ax_p.set_ylabel("Pressure [hPa]")
    ax_p.set_xlabel("Time [s]")
    for ax in (ax_alt, ax_p):
        ax.grid(True, linewidth=0.6)
        ax.set_xlim(0, window_s)
    # 最新値の直接ラベル（各パネル右上の内側）
    lbl_kw = dict(ha="right", va="top", color=COLOR_TEXT, fontsize=12,
                  bbox=dict(boxstyle="round,pad=0.25", facecolor=COLOR_SURFACE, edgecolor="none", alpha=0.85))
    lbl_alt = ax_alt.text(0.99, 0.95, "", transform=ax_alt.transAxes, **lbl_kw)
    lbl_p = ax_p.text(0.99, 0.95, "", transform=ax_p.transAxes, **lbl_kw)
    status = fig.text(0.01, 0.01, "recording...", color=COLOR_TEXT_2, fontsize=9)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))

    state = {"done": False}

    def update() -> None:
        if state["done"]:
            return
        got = False
        while True:
            try:
                item = q.get_nowait()
            except Exception:
                break
            if item is None:
                state["done"] = True
                break
            t, a, p = item
            ts.append(t); alts.append(a); press.append(p)
            got = True
        if got:
            # 表示範囲外の古いデータを捨てる
            t_last = ts[-1]
            while ts and ts[0] < t_last - window_s:
                ts.popleft(); alts.popleft(); press.popleft()
            x0 = max(0.0, t_last - window_s)
            line_alt.set_data(ts, alts)
            line_p.set_data(ts, press)
            ax_p.set_xlim(x0, x0 + window_s)
            _autoscale(ax_alt, alts, MIN_SPAN_ALT)
            _autoscale(ax_p, press, MIN_SPAN_PRESS)
            lbl_alt.set_text(f"{alts[-1]:.2f} m")
            lbl_p.set_text(f"{press[-1]:.3f} hPa")
            fig.canvas.draw_idle()
        if state["done"]:
            status.set_text("finished (close this window to exit)")
            fig.canvas.draw_idle()
            timer.stop()
            if auto_close:
                plt.close(fig)

    timer = fig.canvas.new_timer(interval=PLOT_INTERVAL_MS)
    timer.add_callback(update)
    timer.start()
    plt.show()


if __name__ == "__main__":
    # 疑似データで単体テスト: 高度がゆっくり上下し、気圧がそれに追従する
    import math
    import threading
    import time
    from multiprocessing import Queue as MPQueue

    q: Queue = MPQueue()

    def feeder() -> None:
        t0 = time.perf_counter()
        while (t := time.perf_counter() - t0) < 12:
            alt = 60 + 1.5 * math.sin(t / 2) + 0.1 * math.sin(t * 37)
            q.put((t, alt, 1013.25 * (1 - alt / 44307.7) ** (1 / 0.190284) / 100))
            time.sleep(0.02)
        q.put(None)

    threading.Thread(target=feeder, daemon=True).start()
    run_plot(q, window_s=10, title="demo")
