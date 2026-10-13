# pressure-BMP581

DFRobot Gravity: BMP581 Barometric Pressure Sensor（SEN0665、Bosch BMP581 気圧センサ）を **UART モード**で PC に接続し、
Python で定周期に読み出して温度・気圧・高度を表示・CSV 保存するプログラム。

- 製品 Wiki: https://wiki.dfrobot.com/sen0665/
- 公式ライブラリ（プロトコルの出典）: https://github.com/DFRobot/DFRobot_BMP58X

## ハードウェア設定

- DIP スイッチ（基板右側の 2 連スイッチ）
  | スイッチ | 機能 | 設定 |
  |---|---|---|
  | 1 | I2C / UART 切替 | **UART 側**（シルク「UART」） |
  | 2 | アドレス 0x47 / 0x46 | 0x47（既定）のまま |

  切替は**電源を入れ直した後**に有効になる。
- 配線（UART-USB 変換との接続。Gravity 4 ピンコネクタまたはピンヘッダ）
  | SEN0665 | UART-USB 変換 |
  |---|---|
  | VCC | 3.3V または 5V |
  | GND | GND |
  | D/T (TxD) | RxD |
  | C/R (RxD) | TxD |
- UART: 9600 bps（既定）, 8N1, TTL レベル
- プロトコル: **Modbus RTU**（PC がマスター、センサがスレーブ ID 0x47）。詳細は `bmp581_uart.py` 冒頭のコメント参照

## セットアップ

```
pip install -r requirements.txt
```

pyserial が必須。matplotlib は `--plot`（リアルタイムグラフ）を使うときだけ必要。

## 使い方

### 読み出し・表示・CSV 保存（pressure_read.py）

```
python pressure_read.py                             # COM7, 10 Hz, 表示のみ（同じ位置で更新表示）
python pressure_read.py --port COM3                 # ポート指定
python pressure_read.py --csv                       # pressure_YYYYmmdd_HHMMSS.csv に保存
python pressure_read.py --csv data/run1.csv         # ファイル名指定
python pressure_read.py --duration 10 --csv         # 10 秒間記録して自動終了
python pressure_read.py --rate 25                   # 25 Hz（センサの ODR も同じ値に設定）
python pressure_read.py --display-rate 0 --csv      # 画面表示なしで CSV 記録のみ
python pressure_read.py --sea-level 1010.5          # 高度計算の海面気圧 [hPa] を指定
python pressure_read.py --known-altitude 30         # 現在地の高度 30 m として海面気圧を逆算
python pressure_read.py --osr-p 16 --iir-p 7        # 気圧の OSR 16 倍・IIR 係数 7 でノイズ低減
python pressure_read.py --plot                      # 高度・気圧をリアルタイムにグラフ表示
python pressure_read.py --plot --plot-window 60     # グラフの時間幅を 60 秒に（既定 30 秒）
```

Ctrl+C で終了。サンプリングは `--rate`（既定 10 Hz）、画面表示は `--display-rate`（既定 5 Hz）で間引き。
センサの出力データレート（ODR）は `--rate` に最も近い設定値に自動で合わせる。
端末では 4 行のブロックを同じ位置に更新表示し、`--scroll` またはパイプ時は 1 行ずつ出力する。

CSV の列:
`timestamp`(UNIX 時刻 [s]), `t`(開始からの経過 [s]), `temp`(温度 [°C]), `press`(気圧 [hPa]), `alt`(高度 [m])

高度は国際標準大気の気圧高度式 `alt = 44307.7 × (1 − (p / p0)^0.190284)` で計算する。
p0（海面気圧）は既定 1013.25 hPa。実際の気圧配置で変わるため、絶対高度が必要なときは `--sea-level` に当日の海面気圧を与えるか、
`--known-altitude` で現在地の高度を与えて逆算する。相対的な高度変化（階段の上り下りなど）を見るだけなら既定のままでよい。

### リアルタイムグラフ（--plot）

`--plot` を付けると、高度と気圧を上下 2 段のグラフで直近 `--plot-window` 秒ぶんスクロール表示する（matplotlib を使用）。
グラフの描画は別プロセスで行い、サンプリングループは測定値をキューに渡すだけなので、定周期と CSV 保存には影響しない
（50 Hz・`--plot` 併用で 6 秒間 301 サンプル、間隔のばらつき 0.5 ms 以内を確認）。
記録が終わってもグラフウィンドウは残り、閉じるとプログラムが終了する。グラフを先に閉じても記録は続く。

```
python pressure_read.py --plot --csv                        # 記録しながらグラフ表示
python pressure_read.py --plot --known-altitude 30          # 現在地を 30 m としてグラフ表示
```

高度の変化を見せるデモでは、センサを持って階段を上り下りしたり、床から机の上に持ち上げたりすると 1 m 程度の変化がグラフで読み取れる。
縦軸の最小レンジは高度 2 m・気圧 0.3 hPa で、ノイズだけのときに拡大しすぎないようにしている。

ノイズ低減オプション:
- `--osr-t` / `--osr-p`: オーバーサンプリング倍率（1, 2, 4, …, 128）。大きいほど低ノイズだが測定時間が延び、高い ODR と両立しない（両立しないときは起動時に警告）
- `--iir-t` / `--iir-p`: IIR フィルタ係数（0=なし, 1, 3, 7, …, 127）。大きいほど滑らかだが応答が遅れる

### ボーレート変更（bmp581_set_baud.py）

9600 bps では 1 回の読み出しに約 33 ms かかり、約 30 Hz が上限。より速く読む場合はセンサのボーレートを上げる（115200 bps で 100 Hz まで確認済み）。

```
python bmp581_set_baud.py                          # 現在の設定を表示
python bmp581_set_baud.py 115200                   # 設定を 115200 に変更
python bmp581_set_baud.py 9600 --current 115200    # 115200 で動作中のセンサを 9600 に戻す
```

設定はセンサに保存されるが、**実際の通信速度はセンサの電源を入れ直した後に変わる**（ソフトリセットでは変わらない）。
変更後はケーブルを抜き差しして電源を入れ直し、`pressure_read.py --baud 115200` のように起動する。

## ファイル

- `bmp581_uart.py` … BMP581 UART (Modbus RTU) ドライバ（レジスタ定義、読み書き、`read_all()` 一括取得、ODR/OSR/IIR 設定、高度計算）
- `pressure_read.py` … 定周期読み出し・表示・CSV 保存・グラフ表示（`--plot`）
- `live_plot.py` … リアルタイムグラフ（別プロセスで matplotlib を描画。単体で `python live_plot.py` と実行すると疑似データでデモ）
- `bmp581_set_baud.py` … センサのボーレート設定の表示・変更

## 実測

`read_raw()`（温度 + 気圧 6 レジスタを 1 トランザクションで読み出し）1 回あたりの所要時間と到達できるレート:

| ボーレート | 1 回の読み出し | 結果 |
|---|---|---|
| 9600 bps | 約 33 ms | 10 Hz・25 Hz でエラー・欠落なし。50 Hz を指定しても約 30 Hz で頭打ち |
| 115200 bps | 約 10 ms | 50 Hz で 5 秒間 251 サンプル、100 Hz で 500 サンプル。いずれもエラー・欠落なし |

115200 bps でも 10 ms かかるのは電文の転送時間（約 2 ms）ではなくセンサ基板側の応答待ちが大半のため、100 Hz 付近が実用上の上限。
OSR 1 倍・IIR なしのとき、静置での気圧のばらつきは約 ±0.02 hPa（高度換算で約 ±0.2 m）。

## 注意

- 読み出し周期（`--rate`）がセンサの ODR より速いと同じ値が繰り返し返る。`pressure_read.py` は ODR を `--rate` に合わせて設定するので通常は問題ない。
- 温度はセンサ内部の温度で、基板の自己発熱や PC の排気の影響を受ける。室温の計測には向かない。
- Modbus のレジスタ番地は BMP581 のレジスタ番地に 6 を足したものになる（例: CHIP_ID 0x01 → Modbus 0x07）。
  ボーレート設定レジスタ（Modbus Holding 0x01）だけはオフセットなし。
