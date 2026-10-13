"""
BMP581 (DFRobot SEN0665) の UART ボーレート設定を変更するプログラム。

9600 bps（既定）では 1 回の読み出しに約 33 ms かかり、約 30 Hz が上限になる。
より高速に読みたい場合はセンサ側のボーレートを上げる（115200 bps では約 10 ms、100 Hz まで動作確認済み）。

注意: 設定はセンサに保存されるが、実際の通信速度は**センサの電源を入れ直した後**に変わる
（ソフトリセットでは変わらない）。変更後は USB-UART ケーブルを抜き差しするなどして電源を入れ直すこと。

使い方:
    python bmp581_set_baud.py                          # 現在の設定を表示するだけ
    python bmp581_set_baud.py 115200                   # 設定を 115200 に変更（要・電源再投入）
    python bmp581_set_baud.py 9600 --current 115200    # 115200 で動作中のセンサを 9600 に戻す
    python bmp581_set_baud.py 115200 --port COM3

電源再投入後は pressure_read.py に --baud を付けて起動する:
    python pressure_read.py --baud 115200
"""

from __future__ import annotations

import argparse
import sys
import time

from bmp581_uart import BMP581UART, BMP581Error, BAUD_TABLE


def main() -> int:
    ap = argparse.ArgumentParser(description="BMP581 UART ボーレート設定の表示・変更")
    ap.add_argument("baud", type=int, nargs="?", choices=sorted(BAUD_TABLE), help="新しいボーレート。省略時は現在の設定を表示")
    ap.add_argument("--port", default="COM7", help="シリアルポート (default: COM7)")
    ap.add_argument("--current", type=int, default=9600, help="センサが現在動作しているボーレート (default: 9600)")
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x47, help="Modbus スレーブ ID (default: 0x47)")
    args = ap.parse_args()

    try:
        with BMP581UART(args.port, args.current, args.addr) as bmp:
            bmp.begin(reset=False)
            saved = bmp.get_baud()
            print(f"接続OK（通信速度 {args.current} bps）。センサに保存されている設定: {saved} bps")
            if args.baud is None:
                return 0
            bmp.set_baud(args.baud)
            time.sleep(0.1)
            saved = bmp.get_baud()
    except BMP581Error as e:
        print(f"センサと通信できません（--current {args.current} が実際の速度と違う可能性）: {e}", file=sys.stderr)
        return 1

    if saved != args.baud:
        print(f"設定の書き込みに失敗しました（読み返し: {saved} bps）", file=sys.stderr)
        return 1
    print(f"設定を {args.baud} bps に変更しました。センサの電源を入れ直すと有効になります。")
    print(f"その後は  python pressure_read.py --port {args.port} --baud {args.baud}  で起動してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
