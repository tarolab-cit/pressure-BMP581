"""
DFRobot Gravity: BMP581 Barometric Pressure Sensor (SEN0665) を UART モードで制御するドライバ。

対象ボード: DFRobot SEN0665（DIP スイッチ 1 を UART 側にして UART モード）
UART 仕様: 9600 bps（既定、変更可）, 8N1, TTL レベル

プロトコル: Modbus RTU（PC がマスター、センサがスレーブ）
  スレーブ ID       : 0x47（DIP スイッチ 2 で 0x46 に変更可。I2C アドレスと共通）
  レジスタ対応      : BMP581 のレジスタ番地 R は Modbus レジスタ番地 R + 6 に対応する
                      （Modbus 16bit レジスタ 1 個に BMP581 の 8bit レジスタ 1 個が入る）
  測定値の読み出し  : Function 0x04 (Read Input Registers)
  設定の読み出し    : Function 0x03 (Read Holding Registers)
  設定の書き込み    : Function 0x10 (Write Multiple Registers)
  フレーム          : <ID> <Func> <データ...> <CRC16 (Modbus, 下位バイト先)>

出典: DFRobot 公式ライブラリ https://github.com/DFRobot/DFRobot_BMP58X
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, fields

import serial

# ---------------------------------------------------------------------------
# BMP581 レジスタ（Modbus では +MODBUS_REG_OFFSET した番地になる）
# ---------------------------------------------------------------------------
MODBUS_REG_OFFSET = 0x06

REG_CHIP_ID = 0x01
REG_REV_ID = 0x02
REG_CHIP_STATUS = 0x11
REG_INT_CONFIG = 0x14
REG_INT_SOURCE = 0x15
REG_TEMP_DATA_XLSB = 0x1D     # 0x1D..0x1F 温度 24bit, 0x20..0x22 気圧 24bit（XLSB, LSB, MSB 順）
REG_PRESS_DATA_XLSB = 0x20
REG_INT_STATUS = 0x27
REG_STATUS = 0x28
REG_DSP_CONFIG = 0x30
REG_DSP_IIR = 0x31
REG_OSR_CONFIG = 0x36
REG_ODR_CONFIG = 0x37
REG_OSR_EFF = 0x38
REG_CMD = 0x7E

# Modbus 固有レジスタ（オフセットなし）
MODBUS_REG_PID = 0x00         # Input: 製品 ID
MODBUS_REG_VID = 0x01         # Input: ベンダ ID
MODBUS_REG_BAUD = 0x01        # Holding: UART ボーレート設定

CHIP_ID_BMP581 = 0x50
CHIP_ID_BMP585 = 0x51
SOFT_RESET_CMD = 0xB6

# INT_STATUS (0x27) のビット
INT_DRDY = 0x01
INT_FIFO_FULL = 0x02
INT_FIFO_THRES = 0x04
INT_PRESS_OOR = 0x08
INT_POR_SOFTRESET_COMPLETE = 0x10

# 電源モード（ODR_CONFIG bit1:0）
MODE_SLEEP = 0x00
MODE_NORMAL = 0x01       # ODR で設定した周期で測定
MODE_FORCED = 0x02       # 1 回だけ測定
MODE_CONTINUOUS = 0x03   # 最速で連続測定

# 出力データレート（ODR_CONFIG bit6:2）: Hz → レジスタ値
ODR_TABLE: dict[float, int] = {
    240: 0x00, 218.5: 0x01, 199.1: 0x02, 179.2: 0x03, 160: 0x04, 149.3: 0x05,
    140: 0x06, 129.8: 0x07, 120: 0x08, 110.1: 0x09, 100.2: 0x0A, 89.6: 0x0B,
    80: 0x0C, 70: 0x0D, 60: 0x0E, 50: 0x0F, 45: 0x10, 40: 0x11, 35: 0x12,
    30: 0x13, 25: 0x14, 20: 0x15, 15: 0x16, 10: 0x17, 5: 0x18, 4: 0x19,
    3: 0x1A, 2: 0x1B, 1: 0x1C, 0.5: 0x1D, 0.25: 0x1E, 0.125: 0x1F,
}

# オーバーサンプリング（OSR_CONFIG: 温度 bit2:0, 気圧 bit5:3）: 倍率 → レジスタ値
OSR_TABLE: dict[int, int] = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5, 64: 6, 128: 7}

# IIR フィルタ係数（DSP_IIR: 温度 bit2:0, 気圧 bit5:3）: 係数 → レジスタ値（0 はバイパス）
IIR_TABLE: dict[int, int] = {0: 0, 1: 1, 3: 2, 7: 3, 15: 4, 31: 5, 63: 6, 127: 7}

# UART ボーレート設定（Modbus Holding 0x01）: bps → レジスタ値
BAUD_TABLE: dict[int, int] = {
    2400: 1, 4800: 2, 9600: 3, 14400: 4, 19200: 5, 38400: 6, 57600: 7, 115200: 8,
}

# Modbus 例外コード
MODBUS_EXCEPTION = {
    0x01: "ILLEGAL_FUNCTION",
    0x02: "ILLEGAL_DATA_ADDRESS",
    0x03: "ILLEGAL_DATA_VALUE",
    0x04: "SLAVE_DEVICE_FAILURE",
}

SEA_LEVEL_PA = 101325.0   # 標準大気圧 [Pa]


def crc16_modbus(data: bytes) -> int:
    """Modbus RTU の CRC16（多項式 0xA001, 初期値 0xFFFF）"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def pressure_to_altitude(pressure_pa: float, sea_level_pa: float = SEA_LEVEL_PA) -> float:
    """気圧 [Pa] から高度 [m] を計算（国際標準大気の気圧高度式）"""
    return 44307.7 * (1.0 - (pressure_pa / sea_level_pa) ** 0.190284)


def sea_level_from_altitude(pressure_pa: float, altitude_m: float) -> float:
    """現在地の気圧 [Pa] と既知の高度 [m] から海面気圧 [Pa] を逆算"""
    return pressure_pa / (1.0 - altitude_m / 44307.7) ** (1.0 / 0.190284)


class BMP581Error(Exception):
    """BMP581 通信エラー"""


@dataclass
class PressureData:
    """BMP581 の 1 回分の測定値"""
    t: float          # 取得時刻 [s]（time.perf_counter 基準、開始からの相対時間）
    temp: float       # 温度 [°C]
    press: float      # 気圧 [hPa]
    alt: float        # 高度 [m]（海面気圧基準）

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def as_list(self) -> list:
        return [getattr(self, n) for n in self.field_names()]


class BMP581UART:
    """BMP581 (SEN0665) UART/Modbus RTU ドライバ"""

    def __init__(self, port: str, baudrate: int = 9600, addr: int = 0x47,
                 timeout: float = 0.2, retries: int = 3):
        self.ser = serial.Serial(port, baudrate, bytesize=8, parity="N", stopbits=1,
                                 timeout=timeout, write_timeout=timeout)
        self.addr = addr
        self.retries = retries
        self.sea_level_pa = SEA_LEVEL_PA
        self._t0 = time.perf_counter()
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    # ----- コンテキストマネージャ -----
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    # ----- Modbus RTU 低レベル通信 -----
    def _transaction(self, pdu: bytes, resp_len: int) -> bytes:
        """<ID><pdu><CRC> を送り、resp_len バイト（CRC 含む）の応答を受けて検証する。"""
        req = bytes([self.addr]) + pdu
        req += struct.pack("<H", crc16_modbus(req))
        last_err: Exception | None = None
        for _ in range(self.retries):
            try:
                self.ser.reset_input_buffer()
                self.ser.write(req)
                resp = self.ser.read(resp_len)
                if len(resp) >= 5 and resp[1] & 0x80:
                    # 例外応答: <ID> <Func|0x80> <code> <CRC>
                    code = resp[2]
                    raise BMP581Error(f"Modbus 例外 func=0x{resp[1] & 0x7F:02X}: "
                                      f"{MODBUS_EXCEPTION.get(code, f'0x{code:02X}')}")
                if len(resp) != resp_len:
                    raise BMP581Error(f"タイムアウト: {resp_len} バイト期待, {len(resp)} バイト受信")
                if resp[0] != self.addr or resp[1] != pdu[0]:
                    raise BMP581Error(f"不正な応答ヘッダ: {resp[:2].hex(' ')}")
                if crc16_modbus(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
                    raise BMP581Error("CRC 不一致")
                return resp
            except BMP581Error as e:
                last_err = e
            time.sleep(0.005)
        raise last_err  # type: ignore[misc]

    def read_input_regs(self, mb_reg: int, n: int) -> list[int]:
        """Modbus Input Register を n 個読む（Function 0x04）。Modbus 番地をそのまま指定。"""
        resp = self._transaction(struct.pack(">BHH", 0x04, mb_reg, n), 5 + 2 * n)
        return list(struct.unpack(f">{n}H", resp[3:3 + 2 * n]))

    def read_holding_regs(self, mb_reg: int, n: int) -> list[int]:
        """Modbus Holding Register を n 個読む（Function 0x03）。Modbus 番地をそのまま指定。"""
        resp = self._transaction(struct.pack(">BHH", 0x03, mb_reg, n), 5 + 2 * n)
        return list(struct.unpack(f">{n}H", resp[3:3 + 2 * n]))

    def write_holding_regs(self, mb_reg: int, values: list[int]) -> None:
        """Modbus Holding Register に複数書き込む（Function 0x10）。Modbus 番地をそのまま指定。"""
        n = len(values)
        pdu = struct.pack(">BHHB", 0x10, mb_reg, n, 2 * n) + struct.pack(f">{n}H", *values)
        self._transaction(pdu, 8)

    # ----- BMP581 レジスタアクセス（BMP581 の番地で指定、オフセットは内部で加算） -----
    def read_regs(self, reg: int, length: int) -> bytes:
        """BMP581 レジスタ reg から length バイト読む（データ系は Input Register）"""
        vals = self.read_input_regs(reg + MODBUS_REG_OFFSET, length)
        return bytes(v & 0xFF for v in vals)

    def read_config(self, reg: int) -> int:
        """BMP581 の設定レジスタを 1 バイト読む（Holding Register）"""
        return self.read_holding_regs(reg + MODBUS_REG_OFFSET, 1)[0] & 0xFF

    def write_config(self, reg: int, value: int) -> None:
        """BMP581 の設定レジスタに 1 バイト書く（Holding Register）"""
        self.write_holding_regs(reg + MODBUS_REG_OFFSET, [value & 0xFF])
        time.sleep(0.002)

    def read_reg(self, reg: int) -> int:
        return self.read_regs(reg, 1)[0]

    # ----- 初期化・設定 -----
    def begin(self, reset: bool = True, odr_hz: float = 10, osr_t: int = 1, osr_p: int = 1,
              iir_t: int = 0, iir_p: int = 0, mode: int = MODE_NORMAL) -> None:
        """
        接続確認と測定設定。
        reset=True でソフトリセット後に、気圧測定有効化 → OSR → IIR → ODR → 電源モードの順に設定する。
        """
        chip_id = self.read_reg(REG_CHIP_ID)
        if chip_id not in (CHIP_ID_BMP581, CHIP_ID_BMP585):
            raise BMP581Error(f"CHIP_ID 不一致: 0x{chip_id:02X} (期待 0x{CHIP_ID_BMP581:02X})")

        if reset:
            self.write_config(REG_CMD, SOFT_RESET_CMD)
            time.sleep(0.01)
            status = self.read_reg(REG_INT_STATUS)   # 読むとクリアされる
            if not status & INT_POR_SOFTRESET_COMPLETE:
                raise BMP581Error(f"ソフトリセット未完了 (INT_STATUS=0x{status:02X})")

        self.set_mode(MODE_SLEEP)                     # 設定変更は SLEEP で行う
        self.set_osr(osr_t, osr_p)
        self.set_iir(iir_t, iir_p)
        self.set_odr(odr_hz)
        self.set_mode(mode)
        time.sleep(0.05)

    def set_mode(self, mode: int) -> None:
        """電源モード設定（ODR_CONFIG bit1:0）。deep standby は常に無効化する。"""
        v = self.read_config(REG_ODR_CONFIG)
        v = (v & ~0x03) | (mode & 0x03) | 0x80    # bit7 deep_dis=1
        self.write_config(REG_ODR_CONFIG, v)
        time.sleep(0.005)

    def get_mode(self) -> int:
        return self.read_config(REG_ODR_CONFIG) & 0x03

    def set_odr(self, odr_hz: float) -> float:
        """出力データレートを設定。表にない値は最も近い値に丸め、実際に設定した Hz を返す。"""
        hz = min(ODR_TABLE, key=lambda k: abs(k - odr_hz))
        v = self.read_config(REG_ODR_CONFIG)
        v = (v & ~(0x1F << 2)) | (ODR_TABLE[hz] << 2)
        self.write_config(REG_ODR_CONFIG, v)
        return hz

    def set_osr(self, osr_t: int, osr_p: int) -> None:
        """オーバーサンプリング倍率（1,2,4,...,128）。気圧測定も同時に有効化する。"""
        if osr_t not in OSR_TABLE or osr_p not in OSR_TABLE:
            raise ValueError(f"OSR は {sorted(OSR_TABLE)} のいずれか")
        v = (OSR_TABLE[osr_p] << 3) | OSR_TABLE[osr_t] | 0x40    # bit6 press_en
        self.write_config(REG_OSR_CONFIG, v)

    def set_iir(self, iir_t: int, iir_p: int) -> None:
        """IIR フィルタ係数（0=バイパス, 1,3,7,...,127）。SLEEP モードで設定すること。"""
        if iir_t not in IIR_TABLE or iir_p not in IIR_TABLE:
            raise ValueError(f"IIR は {sorted(IIR_TABLE)} のいずれか")
        dsp = self.read_config(REG_DSP_CONFIG)
        dsp |= (1 << 2) | (1 << 3) | (1 << 5)   # iir_flush_forced_en, shdw_sel_iir_t, shdw_sel_iir_p
        self.write_config(REG_DSP_CONFIG, dsp)
        self.write_config(REG_DSP_IIR, (IIR_TABLE[iir_p] << 3) | IIR_TABLE[iir_t])

    def odr_is_valid(self) -> bool:
        """設定した ODR と OSR の組み合わせが実現可能か（OSR_EFF bit7）"""
        return bool(self.read_reg(REG_OSR_EFF) & 0x80)

    def get_int_status(self) -> int:
        """INT_STATUS (0x27)。読み出すとクリアされる。bit0=DRDY"""
        return self.read_reg(REG_INT_STATUS)

    def set_baud(self, baudrate: int) -> None:
        """
        センサ側の UART ボーレート設定を書き込む。設定は不揮発に保存されるが、
        実際の通信速度はセンサの電源を入れ直すまで変わらない（ソフトリセットでも変わらない）。
        """
        if baudrate not in BAUD_TABLE:
            raise ValueError(f"ボーレートは {sorted(BAUD_TABLE)} のいずれか")
        self.write_holding_regs(MODBUS_REG_BAUD, [BAUD_TABLE[baudrate]])

    def get_baud(self) -> int:
        """センサに保存されているボーレート設定 [bps] を読む（電源再投入後に有効になる値）"""
        code = self.read_holding_regs(MODBUS_REG_BAUD, 1)[0]
        for bps, c in BAUD_TABLE.items():
            if c == code:
                return bps
        raise BMP581Error(f"未知のボーレート設定値: {code}")

    def get_ids(self) -> tuple[int, int, int]:
        """(Modbus PID, Modbus VID, REV_ID)"""
        pid, vid = self.read_input_regs(MODBUS_REG_PID, 2)
        return pid, vid, self.read_reg(REG_REV_ID)

    # ----- 高度の基準 -----
    def set_sea_level(self, sea_level_hpa: float) -> None:
        """高度計算に使う海面気圧 [hPa] を設定"""
        self.sea_level_pa = sea_level_hpa * 100.0

    def calibrate_altitude(self, known_altitude_m: float) -> float:
        """現在地の高度 [m] が既知のとき、現在の気圧から海面気圧を逆算して基準にする。設定した海面気圧 [hPa] を返す。"""
        p = self.read_raw()[1]
        self.sea_level_pa = sea_level_from_altitude(p, known_altitude_m)
        return self.sea_level_pa / 100.0

    # ----- データ取得 -----
    @staticmethod
    def _s24(b: bytes) -> int:
        """XLSB, LSB, MSB の 3 バイトを 24bit 符号付き整数に"""
        v = b[0] | (b[1] << 8) | (b[2] << 16)
        return v - (1 << 24) if v & 0x800000 else v

    def read_raw(self) -> tuple[float, float]:
        """温度 [°C] と気圧 [Pa] を 1 トランザクションで読む"""
        raw = self.read_regs(REG_TEMP_DATA_XLSB, 6)
        temp = self._s24(raw[0:3]) / 65536.0
        press = self._s24(raw[3:6]) / 64.0
        return temp, press

    def read_all(self) -> PressureData:
        """温度・気圧を読み、高度を計算して PressureData を返す"""
        t = time.perf_counter() - self._t0
        temp, press_pa = self.read_raw()
        return PressureData(t, temp, press_pa / 100.0, pressure_to_altitude(press_pa, self.sea_level_pa))
