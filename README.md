# pressure-BMP581

DFRobot Gravity: BMP581 気圧センサ（SEN0665）を UART で PC につなぎ、Python で温度・気圧・高度を読み出して表示・CSV 保存・グラフ表示するプログラム。

- 製品 Wiki: https://wiki.dfrobot.com/sen0665/
- 公式ライブラリ（プロトコルの出典）: https://github.com/DFRobot/DFRobot_BMP58X

## 配線

- DIP スイッチ 1 を **UART 側**にする（切替は電源を入れ直すと有効）。スイッチ 2 は既定（0x47）のまま
- UART-USB 変換器との接続（9600 bps, 8N1）

| SEN0665 | UART-USB 変換 |
|---|---|
| VCC | 3.3V または 5V |
| GND | GND |
| D/T | RxD |
| C/R | TxD |

## セットアップ

```
pip install -r requirements.txt
```

matplotlib は `--plot` を使うときだけ必要。

ポート名は環境ごとに `--port` で指定する（既定は `COM7`）。

| OS | ポート名の例 | 備考 |
|---|---|---|
| Windows | `COM7` | デバイスマネージャーの「ポート (COM と LPT)」で確認 |
| Linux | `/dev/ttyUSB0` | `sudo usermod -aG dialout $USER` で権限付与。グラフ表示には `python3-tk` が必要 |
| macOS | `/dev/cu.usbserial-XXXX` | `ls /dev/cu.*` で確認 |
| WSL | `/dev/ttyUSB0` | [Install_WSL.md](Install_WSL.md) を参照 |

## 使い方

### 読み出し（pressure_read.py）

```
python pressure_read.py --port COM7                        # 表示のみ
python pressure_read.py --port COM7 --csv                  # pressure_YYYYmmdd_HHMMSS.csv に保存
python pressure_read.py --port COM7 --csv run1.csv         # ファイル名指定
python pressure_read.py --port COM7 --duration 10 --csv    # 10 秒で自動終了
python pressure_read.py --port COM7 --plot                 # 高度・気圧をリアルタイム表示
python pressure_read.py --port COM7 --known-altitude 30    # 現在地を高度 30 m として海面気圧を補正
python pressure_read.py --port COM7 --osr-p 16 --iir-p 7   # オーバーサンプリングと IIR フィルタでノイズ低減
```

Ctrl+C で終了。サンプリングは 10 Hz（`--rate` で変更。センサの ODR も自動で合わせる）。
9600 bps では約 30 Hz が上限なので、それ以上は後述のボーレート変更を行う。

CSV の列: `timestamp`(UNIX 時刻), `t`(経過秒), `temp`[°C], `press`[hPa], `alt`[m]

高度は海面気圧 1013.25 hPa を基準にした気圧高度。相対的な変化を見るだけなら既定のままでよく、
絶対高度が必要なときは `--sea-level` に当日の海面気圧を与えるか、`--known-altitude` で現在地の高度を与える。

ノイズ低減オプション（値の一覧は `python pressure_read.py -h`）:

| オプション | 内容 |
|---|---|
| `--osr-t` / `--osr-p` | オーバーサンプリング倍率（1〜128）。大きいほど低ノイズだが高い ODR と両立しない |
| `--iir-t` / `--iir-p` | IIR フィルタ係数（0〜127）。大きいほど滑らかだが応答が遅れる |

### ボーレート変更（bmp581_set_baud.py）

```
python bmp581_set_baud.py --port COM7                            # 現在の設定を表示
python bmp581_set_baud.py --port COM7 115200                     # 115200 bps に変更
python bmp581_set_baud.py --port COM7 9600 --current 115200      # 9600 bps に戻す
```

**変更はセンサの電源を入れ直すと有効になる**（ケーブルを抜き差しする）。
その後は `pressure_read.py --baud 115200` のように起動する。115200 bps で 100 Hz まで動作確認済み。

## ファイル

- `pressure_read.py` … 読み出し・表示・CSV 保存・グラフ表示
- `bmp581_set_baud.py` … センサのボーレート設定
- `bmp581_uart.py` … BMP581 UART (Modbus RTU) ドライバ
- `live_plot.py` … リアルタイムグラフ（別プロセスで描画）
