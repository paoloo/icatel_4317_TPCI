#!/usr/bin/env python3
"""
Icatel-43.17.bin  -- 8051 firmware of the 2004 TPCI/ICATEL public payphone (Brazil).

Python re-implementation for understanding.  Every code section quotes the real
8051 disassembly (radare2, `r2 -a 8051`, full listing in Icatel-43.17.r2.asm)
as comments, followed by an explanation of what it does.

Image layout (65536 bytes = full 8051 external space):
    0x0000-0x7FFF  code (banked via P2, see set_bank)
    0x8000-0x8FFF  windowed I/O: LCD 0x8000-0x8003, latches 0x8040/0x8060/0x8080/0x80C0
    0x9000-0xDFFF  data tables (strings, tariff records) -- banked
    0xE000-0xEFFF  interrupt/keyboard controller regs
    0xFFxx         erased (0xFF)

Interrupt vectors (byte-verified):
    0x0000 RST  -> 0x0033   reset routine
    0x0003 INT0 -> 0x41F7   keyboard (reads 0xE002)
    0x000B T0   -> 0x1B9A   1.034 ms tick, state dispatch table 0x1BC6
    0x0013 INT1 -> 0x103D   bare RETI (unused)
    0x001B T1   -> 0x2EF6   line-signal / charge-pulse generator (tick-reloaded)
    0x0023 UART -> 0x4264   binary supervisor/charge protocol framing

Hardware:
    XTAL 11.0592 MHz [INFERENCE]; machine cycle 921.6 kHz (fosc/12)
    LCD  2x40 HD44780-class, dual interface (see lcd_* below)
    I2C  24C32/24C64 EEPROM, SCL=P3.3 SDA=P1.7, dev 0xA0/A1, 2-byte addr, 64B pages
"""

from __future__ import annotations
import time
from typing import List, Optional, Tuple

# ============================================================================
# Memory model
# ============================================================================

class Memory:
    """8051 XDATA (external RAM + memory-mapped I/O) model."""

    def __init__(self) -> None:
        self.xdata: bytearray = bytearray(0x10000)
        self.xdata[0x2000:] = b"\xff" * 0xE000      # erased flash tail
        # ---- persistent parameter blocks (checksummed, see block_checksum) --
        # block@0x0000: 23 data + 2 trailer; block@0x0019: 93+2; block@0x0078: 22+2
        # ---- memory mapped I/O -------------------------------------------
        self.p1: int = 0xFF        # port 1: .3/.7 = serial bus, .4-.7 = LCD bit-bang
        self.p3: int = 0xFF        # port 3: .3 = I2C SCL
        self.p2: int = 0x00        # port 2 = bank/page latch (set_bank)
        self.idle_mode: bool = False   # PCON.0 (0x87.0)
        self.smod: bool = True         # PCON.7 (0x87.7), written 0x80 at 0x0051
        self.wakeup: bool = False      # any-interrupt-wakes-idle edge

    # -- generic access ------------------------------------------------------
    def rd(self, addr: int) -> int:
        if addr == 0x0708:                       # command mailbox (see mailbox)
            return self.xdata[addr]
        if 0x8000 <= addr < 0x8004:              # LCD window
            return self.lcd_read(addr)
        return self.xdata[addr]

    def wr(self, addr: int, val: int) -> None:
        val &= 0xFF
        if addr == 0x0087:                       # PCON
            self.idle_mode = bool(val & 1)
            if not self.idle_mode:
                self.wakeup = False
            return
        if 0x8000 <= addr < 0x8004:              # LCD window
            self.lcd_write(addr, val)
            return
        self.xdata[addr] = val

    # -- LCD window (0x8000 instr, 0x8001 data, 0x8002 busy, 0x8003 read) ----
    def lcd_read(self, addr: int) -> int:
        if addr == 0x8002:                       # busy flag: bit7; never busy here
            return 0x00
        return self.xdata[addr]

    def lcd_write(self, addr: int, val: int) -> None:
        if addr == 0x8000 and val in (0x01, 0x80, 0xC0):   # clear / DDRAM set
            self.xdata[addr] = val
        # data writes land in the emulated line buffers (see Lcd below)

    # -- I2C bus pins (SCL=P3.3, SDA=P1.7) ----------------------------------
    def scl(self, level: int) -> None:
        self.p3 = (self.p3 | 8) if level else (self.p3 & ~8)

    def sda(self, level: int) -> None:
        self.p1 = (self.p1 | 0x80) if level else (self.p1 & ~0x80)

    def sda_in(self) -> int:
        return (self.p1 >> 7) & 1


# ============================================================================
# Low-level timing: Timer0 tick and the PCON-idle delay  (0x7B6A)
# ============================================================================

XTAL_HZ = 11_059_200                       # [INFERENCE] standard UART crystal
CYCLES_PER_TICK = 0x10000 - 0xFC47         # 953 timer clocks (reload 0x1B9A)
TICK_MS = CYCLES_PER_TICK * 12 / XTAL_HZ   # ~1.034 ms

class Timers:
    def __init__(self, mem: Memory) -> None:
        self.mem = mem
        self.tick_count = 0

    def timer0_isr(self) -> None:
        """Vector 0x000B -> 0x1B9A.

        asm @0x1B9A: 75 8C FC  MOV TH0,#0xFC ; 75 8A 47  MOV TL0,#0x47
                     ... dispatch on IRAM 0x1A through table 0x1BC6 ...
        asm @0x1ECD: D2 33     SETB 0x26.3    ; the ONLY setter of this bit in the image
        Every tick re-arms the reload (953 cycles ~ 1.034 ms) and raises the
        tick flag 0x26.3 that delay_ticks/idle-waiters consume.
        """
        self.tick_count += 1
        self.mem.xdata[0] |= 0  # (state dispatch side effects omitted in model)
        self.mem.wakeup = True           # wakes PCON idle

    def wait_units(self, units: int) -> None:
        """0x7B6A: idle until `units` ticks elapsed.

        asm:  loop: ORL PCON,#1  ; CMOS idle, woken by next interrupt
              JNB 0x26.3, loop   ; wait tick flag
              CLR 0x2A.7 / CPL 0x2A.7 ; alternates subtracting 5,6,5,6...
              SUBB A,R0 ... DJNZ on 16-bit DPTR counter
        One unit = 5 or 6 ticks alternating => average 5.5 ticks (~5.7 ms).
        """
        for _ in range(units):
            pass  # in emulation: Timers.tick_count += 5.5 on average


# ============================================================================
# Datastore: three checksummed blocks + 24Cxx EEPROM shadow
# ============================================================================

BLOCK_SENTRIES = (0x17, 0x76, 0x8C)        # 0x7D56 selects bases from these
BLOCK_BASES   = (0x0000, 0x0019, 0x0078)

def select_block(dpl: int) -> Optional[int]:
    """0x7D56: map DPL id -> block base.

    asm: MOV R7,#0 ... CJNE DPL,#0x17 -> base 0x0000
         DPL<=0x76 -> base 0x0019 ; DPL<=0x8C -> base 0x0078 ; else CLR C (fail)
    """
    if dpl <= BLOCK_SENTRIES[0]:
        return BLOCK_BASES[0]
    if dpl <= BLOCK_SENTRIES[1]:
        return BLOCK_BASES[1]
    if dpl <= BLOCK_SENTRIES[2]:
        return BLOCK_BASES[2]
    return None


def block_checksum(mem: Memory, addr: int, length: int) -> int:
    """0x7D9D: plain 16-bit running sum, hi in IRAM 0x0F, lo in 0x0E.

    asm: E4 CLR A ; F5 0E MOV 0x0E,A ; F5 0F MOV 0x0F,A
         loop: MOVX A,@DPTR ; 25 0E ADD A,0x0E ; F5 0E ; 50 02 JNC +2 ; 05 0F INC 0x0F
               A3 INC DPTR ; DC F4 DJNZ R4
    """
    total = 0
    for i in range(length):
        total += mem.xdata[addr + i]
    return total & 0xFFFF


def block_verify(mem: Memory, addr: int, length: int) -> bool:
    """0x7DAF: checksum data then compare 2-byte trailer [sum_hi][sum_lo].

    asm: LCALL 0x7D9D ; MOVX A,@DPTR ; CJNE A,0x0F (hi) ; INC DPTR
         MOVX A,@DPTR ; CJNE A,0x0E (lo) ; SETB C / CLR C ; RET
    """
    s = block_checksum(mem, addr, length)
    return mem.xdata[addr + length] == (s >> 8) and mem.xdata[addr + length + 1] == (s & 0xFF)


class I2cEeprom:
    """Bit-banged 24C32/24C64: SCL=P3.3, SDA=P1.7, opcode 0xA0/0xA1.

    Verified primitives (e.g. @0x805A-0x8061):
        START: CLR SCL ; SETB SDA ; SETB SCL ; NOP ; CLR SDA
        STOP : CLR SCL ; CLR SDA ; SETB SCL ; NOP ; SETB SDA
        bit  : RLC A ; CLR SCL ; MOV SDA,C ; SETB SCL ; NOP      (MSB first)
        ack  : SETB SDA ; SETB SCL ; NOP ; JNB SDA,ok            (2 samples)
    Addressing: device 0x50 => two address bytes, hi first (r7=hi, r6=lo).
    Pages: 64 bytes -- write loop computes B = 0x40 - (r6 & 0x3F) @0x85FE.
    """

    def __init__(self, mem: Memory) -> None:
        self.mem = mem
        self.storage: bytearray = bytearray(0x2000)     # 8KB window
        self.mutex = False                              # bit 0x29.5

    def _start(self) -> None:
        self.mem.scl(0); self.mem.sda(1); self.mem.scl(1); self.mem.sda(0)

    def _stop(self) -> None:
        self.mem.scl(0); self.mem.sda(0); self.mem.scl(1); self.mem.sda(1)

    def _byte(self, b: int) -> bool:
        for i in range(7, -1, -1):
            self.mem.scl(0)
            self.mem.sda((b >> i) & 1)
            self.mem.scl(1)
        return True                                     # device always acks here

    # -- 0x8050: random read -------------------------------------------------
    def read_byte(self, addr: int) -> Optional[int]:
        self.mutex = True
        self._start(); self._byte(0xA0)
        self._byte((addr >> 8) & 0xFF); self._byte(addr & 0xFF)
        self._start(); self._byte(0xA1)                 # repeated START + read opcode
        v = 0
        for _ in range(8):
            self.mem.scl(0); self.mem.scl(1)
            v = (v << 1) | self.mem.sda_in()
        self._stop()                                    # master NACK + STOP
        self.mutex = False
        return v

    # -- 0x843E: sequential read block ---------------------------------------
    def read_block(self, addr: int, length: int) -> bytes:
        out = bytearray()
        for i in range(length):
            out.append(self.read_byte(addr + i) or 0)
        return bytes(out)

    # -- 0x85F4 (alias 0x8238): page write with tWr ack-poll (40 tries) ------
    def write_block(self, addr: int, data: bytes) -> bool:
        i, n = 0, len(data)
        while i < n:
            page_room = 0x40 - (addr & 0x3F)            # @0x85FE MOV B,#0x40 ... ANL 0x06,#0x3F
            chunk = data[i:i + min(page_room, n - i)]
            self._start(); self._byte(0xA0)
            self._byte((addr >> 8) & 0xFF); self._byte(addr & 0xFF)
            for b in chunk:
                self._byte(b)
            self._stop()
            addr += len(chunk); i += len(chunk)          # carry -> fresh header next page
        return True

    def write_byte(self, addr: int, val: int) -> bool:   # 0x7E48 single-byte form
        return self.write_block(addr, bytes([val]))


class Datastore:
    """0x7E18/0x7E1E protected-variable commits.
    Contract (byte-verified): the CALLER first mutates XDATA, then calls
    0x7E18 (byte) / 0x7E1E (range) which:
        SETB 0x29.5 (mutex) -> 0x7D56 select block -> 0x7DAF verify trailer
        -> stale? 0x7DC3 rewrite whole block [data+trailer] into EEPROM @0x8238
        -> address outside the 3 blocks? 0x7E48 direct EEPROM byte write.
    So XRAM is the primary copy, EEPROM the shadow; boot restores from EEPROM
    when a block trailer fails (0x010B boot loop, R1=4 attempts).
    """

    def __init__(self, mem: Memory, eep: I2cEeprom) -> None:
        self.mem, self.eep = mem, eep

    def commit(self, addr: int, length: int = 1) -> None:
        base = select_block(addr & 0xFF)
        if base is None:
            self.eep.write_byte(addr, self.mem.xdata[addr])       # fallback @0x7E48
            return
        end = base + {BLOCK_BASES[0]: 25, BLOCK_BASES[1]: 95, BLOCK_BASES[2]: 24}[base]
        if not block_verify(self.mem, base, end - base - 2):      # @0x7DAF
            s = block_checksum(self.mem, base, end - base - 2)
            self.mem.xdata[end - 2] = s >> 8                      # @0x7DC3 writes 0x0F
            self.mem.xdata[end - 1] = s & 0xFF                    #          then 0x0E
            self.eep.write_block(base, self.mem.xdata[base:end])  # @0x8238 whole block


# ============================================================================
# Command mailbox 0x0708 + P1 command bus  (0x4150/0x415D, mixer 0x40EA)
# ============================================================================

class Mailbox:
    """Command byte shared foreground<->ISR; also driven on PORT P1 pins.

    0x4150: PUSH DPH ; PUSH DPL ; MOV DPTR,#0x0708 ; MOVX A,@DPTR ; POP ; POP ; RET
    0x415D: ... MOVX @DPTR,A ; MOV 0xA0,A ; POP ; POP ; RET
            ^ the write is mirrored to P2? No -- 0xA0 is PORT 1 (P1): every
            command value is broadcast on P1.0-P1.7 to the board wiring.
            [CROSSING] P1.7 is simultaneously the EEPROM SDA pin; the hardware
            muxes it (SDA only driven while 0x29.5/I2C driver is active).
    """

    def __init__(self, mem: Memory) -> None:
        self.mem = mem

    def read(self) -> int:                       # 0x4150
        return self.mem.xdata[0x0708]

    def write(self, val: int) -> None:           # 0x415D
        self.mem.xdata[0x0708] = val & 0xFF
        self.mem.p1 = val & 0xFF                 # MOV 0xA0,A  (port 1 mirror)

    @staticmethod
    def mix(buf: bytearray) -> None:
        """0x40EA: 16-bit LFSR keystream mixer over DPTR..+count.

        asm per bit (R = 0x0F:0x0E, taps 0x73=0x40 / 0x74=0x02):
            MOV 0x75,A ; MOV A,#0x0F ; XRL 0x75,A ; ANL A,#1 -> tap = bit0(byte^R_lo)
            JNZ -> XRL 0x0F,#0x40 ; XRL 0x0E,#0x02      (R ^= 0x0240)
            CLR C ; RRC 0x75 ...  MOV 0x0E,0x75          (R = ror16(R))
            MOV A,R1 ; CLR C ; RRC A                     (byte = ror8(byte))
        Self-inverse: decode = encode.
        """
        r = 0
        for i in range(len(buf)):
            b = buf[i]
            for _ in range(8):
                tap = (b ^ (r & 0xFF)) & 1
                if tap:
                    r ^= 0x0240
                r = ((r >> 1) | ((r & 1) << 15)) & 0xFFFF
                b = ((b >> 1) | ((b & 1) << 7)) & 0xFF
            buf[i] = b


# ============================================================================
# HD44780-class LCD (2x40)
# ============================================================================

class Lcd:
    """Two interfaces, both byte-verified:

    A) reset-time bit-bang on P1.4-.7 (0x751F-0x760B):
       P1.4 strobe, P1.5 clock, P1.6 data-out, P1.7 data-in.
       0x7550: SETB P1.4 ; shift-out A ; shift-in 8 bits ; CLR P1.4
       0x758F: probe cmd 0x30, busy = bit4 ; magic (st&0x0A)==0x0A => present.
    B) runtime memory-mapped window 0x8000-0x8003 (instr/data/busy/read).
       0x78CE: poll 0x8002 bit7 up to 0xC1=193 times, then hard reset @0x2FCF.
       line1 DDRAM base 0x80 (40 cols), line2 base 0xC0.
    """

    def __init__(self) -> None:
        self.line1: List[str] = [" "] * 40
        self.line2: List[str] = [" "] * 40
        self.present = True
        self.ddram = 0x00            # current DDRAM cursor (0x80=line1, 0xC0=line2)

    def _render(self, ddr_addr: int, ch: str) -> None:
        buf = self.line1 if ddr_addr < 0xC0 else self.line2
        col = (ddr_addr - (0x80 if ddr_addr < 0xC0 else 0xC0)) % 40
        buf[col] = ch
        self.ddram = ddr_addr + 1    # HD44780 auto-increment

    def instr(self, a: int) -> None:                       # 0x78CE tail
        if a == 0x01:
            self.line1 = [" "] * 40; self.line2 = [" "] * 40

    def put_data(self, byte: int) -> None:                 # 0x792A
        self._render(self.ddram, CHARSET.get(byte, chr(byte)))

    def text(self) -> str:
        return "".join(self.line1).rstrip() + "\n" + "".join(self.line2).rstrip()


# custom LCD charset: 0x04..0x07 are Portuguese accented-glyph codes seen in
# strings ('COMUNICA\x04\x07O' = "COMUNICAÇÃO"), 0x03 = soft hyphen-ish glyph.
CHARSET = {0x03: "­", 0x04: "Ç", 0x05: "Á", 0x06: "Á", 0x07: "Ã"}


# ============================================================================
# Serial protocol (vector 0x0023 -> 0x4264)
# ============================================================================

FRAME_STARTERS = {0x2C, 0x2D, 0x03, 0x0E, 0x22, 0x25, 0x28, 0x29, 0x2E}
# 0x4264: idle branch CJNE with the table above; 0x2C = charge-pulse command.

def crc16_frame(data: bytes) -> int:
    """0x40EA serial CRC: init 0x0000, feedback 0x0240, MSB-first.

    (Called by RX validate @0x42FF and TX append @0x4247; result hi/lo in
     IRAM 0x0F/0x0E. Byte-verified constants 0x73/0x74... [INFERENCE] exact
     polynomial share with the mailbox mixer family.)
    """
    r = 0
    for b in data:
        for _ in range(8):
            bit = (b >> 7) & 1
            b = (b << 1) & 0xFF
            msb = (r >> 15) & 1
            r = ((r << 1) | bit) & 0xFFFF
            if msb:
                r ^= 0x0240
    return r


class Serial:
    """UART mode 1 (8N1), SMOD=1, RX gated by REN (software flow control).

    Baud [INFERENCE]: T1 reload 0xFA -> 9600 (the only standard-rate value);
    other reload pairs (0xF9/0x2D, 0xFF/0x1D, 0xFA/0x10, 0xF7) feed the
    charge-pulse one-shot phases instead (T1 mode set per phase).
    """

    def __init__(self, mem: Memory) -> None:
        self.mem = mem
        self.rx_buf = bytearray()          # XDATA 0x0400 linear + idx 0x7D
        self.tx_buf = bytearray()          # XDATA 0x0300, read idx 0x7E, end 0x7A
        self.rx_state = 0                  # 0x7C: 0=idle, 0xFF=collecting

    def on_rx_byte(self, b: int) -> None:
        """0x4264 RX side."""
        if self.rx_state == 0:
            if b in FRAME_STARTERS:
                self.rx_state = 0xFF
                self.rx_buf = bytearray([b])
            return
        self.rx_buf.append(b)
        if len(self.rx_buf) == 2:          # count field + 2 [INFERENCE]
            self.expected = self.rx_buf[1] + 2
        if len(self.rx_buf) >= getattr(self, "expected", 4):
            frame, trailer = self.rx_buf[:-2], self.rx_buf[-2:]
            ok = (crc16_frame(frame) == (trailer[0] << 8 | trailer[1]))
            self.rx_state = 0
            self.ren = False               # 0x437D CLR REN until re-armed
            if ok and frame[0] == 0x2C:
                self.mem.xdata[0x21] |= 0x01   # SETB 0x21.0: charge pending,
                # consumed by T1 ISR 0x2F1A (pulse 0x11 then 0x80 on latch 0x8080)

    def tx_frame(self, frame: bytearray) -> None:
        """0x4239: append CRC, claim TX flag 0x22.1, start via SBUF."""
        crc = crc16_frame(bytes(frame))
        frame += bytes([crc >> 8, crc & 0xFF])
        self.tx_buf = frame


# ============================================================================
# Keypad  (INT0 vector 0x0003 -> 0x41F7, scan via 0xE002)
# ============================================================================

def keypad_decode(scan: int) -> Optional[int]:
    """0x72C9 family: 0x0A->0, 0x0B/0x0C = function keys, >=0x0D ignored."""
    if scan == 0x0A:
        return 0
    if scan < 0x0A:
        return scan
    return None


# ============================================================================
# Tariff / call engine
# ============================================================================

class Payphone:
    """Main-loop state machine (dispatch @0x026B) and Timer0-tick sub-states.

    Persistent XDATA variables (byte-verified usage):
        0x0013/0x0014  call phase pair (2=waiting,3=connected,4=dialing,
                       7=hang-up req,8=out-of-service,0x0B,0x21=line fault)
        0x076E call_state (0 idle,1 active,2 settle,3 hang-up,4 ended)
        0x076F tone profile (0x0D87 writes it + 5-byte record to 0x0000)
        0x087D-0x087F  call clock (s/min, mod-60 via 0x789C)
        0x0783-0x078D  dialed-digit buffer (cleared by 0x3ED4)
        0x07FD/0x0A03  metering-pulse flags (div-6 @0x0771 -> unit counters)
    """

    def __init__(self) -> None:
        self.mem = Memory()
        self.timers = Timers(self.mem)
        self.eep = I2cEeprom(self.mem)
        self.store = Datastore(self.mem, self.eep)
        self.mail = Mailbox(self.mem)
        self.lcd = Lcd()
        self.uart = Serial(self.mem)
        self.call_state = 0          # 0x076E
        self.phase = 0               # 0x0013 (0x0014 mirrors it)
        self.tone_profile = 0        # 0x076F
        self.clock_sec = 0           # 0x087F
        self.clock_min = 0           # 0x087E
        self.units = 0               # units consumed this call (0x009E/0x009F)
        self.credit_units = 0        # prepaid units on the card (0x3B20 pool)
        self.card_ok = False

    # -- boot -----------------------------------------------------------------
    def reset(self) -> None:
        """Vector 0x0000 -> 0x0033.

        asm 0x0033: C2 AF CLR EA ; 74 B6 MOV A,#0xB6 ; 90 80 60 MOV DPTR,#0x8060
                    F0 MOVX @DPTR,A ; F5 2D MOV 0x2D,A ; 75 90 CF MOV P1,#0xCF
                    C2 B3 CLR P3.3 ; D2 B4 SETB P3.4 ; 75 A8 00 MOV IE,#0
                    75 81 31 MOV SP,#0x31 ; 12 1A C6 LCALL 0x1AC6 (config latch)
                    75 87 80 MOV PCON,#0x80 (SMOD) ; ... 75 89 00 TMOD, 75 98 00 SCON
                    ... zero IRAM 0x32-0x00 and 0x7F-0x68 ; fill 0x68-0x32 with 0xA5
                    ... 12 0D 11 LCALL 0x0D11 (T0 0xFC47, SCON 0x40, IE 0x83)
                    ... 12 75 83 (LCD probe) ; 12 3E C0 (init tags)
                    ... 12 84 3E restore param block from EEPROM if CRC bad
                    ... 01 0B AJMP boot-loop (verify 3 blocks, x4 retries)
        """
        m = self.mem
        m.wr(0x8060, 0xB6); m.p1 = 0xCF; m.scl(0)      # latch images
        self.mail.write(0)                              # mailbox armed idle
        # 0x0D11: TMOD=0x01, TH0=0xFC TL0=0x47, SCON=0x40, TR0, IE=0x83
        # 0x0D44: fill 0x06A0..0x06AF with 0x2B (0x7C38 memset)
        for a in range(0x06A0, 0x06B0):
            m.xdata[a] = 0x2B
        # 0x3EC0: XDATA 0x0807='S',0x05 ; zero 0x077D..0x077F
        m.xdata[0x0807], m.xdata[0x0808] = 0x53, 0x05
        for a in (0x077D, 0x077E, 0x077F):
            m.xdata[a] = 0x00
        # boot loop 0x010B: verify blocks (0x0000 len 0x17, 0x0019 len 0x76,
        # 0x0078 len 0x8C) -- on failure restore from EEPROM (0x843E) and
        # increment corruption counter 0x06C3.
        for base, sentry in zip(BLOCK_BASES, BLOCK_SENTRIES):
            size = sentry - base + 2
            if not block_verify(m, base, size - 2):
                m.xdata[base:size] = self.eep.read_block(base, size)
                m.xdata[0x06C3] = (m.xdata[0x06C3] + 1) & 0xFF
        self.call_state = 0
        self.phase = 0

    # -- tone/relay profile 0x0D87 ---------------------------------------------
    def set_tone_profile(self, a: int) -> None:
        """0x0D87: writes A to 0x076F, TONE_TABLE[A] to latch 0x06B0, and the
        5-byte per-profile record from code table 0x8AC4 into XDATA 0x0000-4
        (then commits the session block via 0x7E1E).

        asm: 90 07 6F MOV DPTR,#0x076F ; F0 ; F9 MOV R1,A
             90 8A EE MOV DPTR,#0x8AEE ; 93 MOVC A,@A+DPTR ; 90 06 B0 ... F0
             MOV A,R1 ; 14 DEC A ; F5 F0 MOV B,A ; 90 8A C3 ; ... MUL AB
             ; copy 5 bytes CODE->XRAM 0x0000, then LCALL 0x7E1E
        """
        tone_table = [0x01, 0x3F, 0x09, 0x01, 0x01, 0x3F, 0x01, 0x01,
                      0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09,
                      0x0B, 0x0A, 0x0C, 0x0D, 0x0E, 0x0F, 0xFF]
        self.tone_profile = a
        self.mem.xdata[0x06B0] = tone_table[a % len(tone_table)]
        self.mem.xdata[0x0000:0x0005] = bytes([a & 0xFF, 0, 0, 0, 0])
        self.store.commit(0x0000, 5)

    # -- call timer tick 0x4DE2 --------------------------------------------------
    def tick_call_clock(self) -> None:
        """0x4DE2: advance 0x087F (sec) with mod-60 carry into 0x087E (min)
        (helper 0x789C), then mirror the 3-byte clock into the session block
        shadow at 0x003D-0x003F and commit (0x3D58 + 0x7E1E).
        """
        self.clock_sec += 1
        if self.clock_sec >= 60:
            self.clock_sec = 0
            self.clock_min = (self.clock_min + 1) % 60
        self.mem.xdata[0x087E] = self.clock_min
        self.mem.xdata[0x087F] = self.clock_sec
        self.mem.xdata[0x003D:0x0040] = bytes([0, self.clock_min, self.clock_sec])
        # tariff metering (0x3B20 + 0x0CCA): while connected, 1 unit per 6 s.
        # 0x0771 is the divide-by-6 pulse divider in the firmware.
        if self.call_state == 2 and self.phase == 3:
            self._sec_divider = getattr(self, "_sec_divider", 0) + 1
            if self._sec_divider >= 6:
                self._sec_divider = 0
                self._burn_unit()

    # -- main loop dispatch 0x026B ----------------------------------------------
    def dispatch(self) -> None:
        """0x026B: MOV DPTR,#0x076E ; MOVX A,@DPTR ; MOV DPTR,#0x0277
                    MOV B,#3 ; MUL AB ; JMP @A+DPTR
        Table 0x0277: state0->0x0426  state1->0x0286  state2->0x0401
                      state3->0x0423  state4->0x0426
        NOTE: A = state*3 stays live; 0x0DC9 (see below) ignores it and
        always returns C=1, so feature-gate branches are dead in this build.
        """
        handler = {
            0: self.state_idle_or_ended,
            1: self.state_call_active,
            2: self.state_settle,
            3: self.state_hangup,
            4: self.state_idle_or_ended,
        }.get(self.call_state, self.state_call_active)
        handler()

    # -- feature-gate stub 0x0DC9 ------------------------------------------------
    @staticmethod
    def stub_true() -> bool:
        """0x0DC9: PUSH ACC ; MOV A,#1 ; RRC A ; POP ACC ; RET
        RRC of A=1 shifts bit0 into C => returns C=1 UNCONDITIONALLY.
        (A preserved by push/pop.) All `LCALL 0x0DC9` conditionals compile
        out; the false branches (0x028B, 0x0417, 0x071E, ...) are dead code.
        """
        return True

    @staticmethod
    def call_flag_set(mem: Memory) -> bool:
        """0x0DD3: PUSH ACC/DPL/DPH ; DPTR=0x0075 ; MOVX A,@DPTR ;
        JZ +3 ; SETB C ... => C = (XDATA[0x0075] != 0).  No scrambling."""
        return mem.xdata[0x0075] != 0

    # -- state handlers -----------------------------------------------------------
    def state_idle_or_ended(self) -> None:
        """0x0426: phase==8 -> 0x073C (hang-up release) else 0x0433
        (play state message / tone for 4 s: LCALL 0x7B6A with DPTR=0x0FA0).
        """
        if self.phase == 8:
            self.hang_up_release_line()
        else:
            self.lcd.instr(0x01)                      # clear (0x7A2B)
            self.lcd.ddram = 0xC0                     # line-2 message "FORA DE OPERACAO"
            for ch in "FORA DE OPERACAO":
                self.lcd.put_data(ord(ch))

    def state_call_active(self) -> None:
        """0x0286-0x0400 (summarized flow, all branches byte-verified):
        hook/credit gate waits XDATA[0x0724]|0x00B0 settle while PCON-idle
        (@0x029E loop, delay 0x03FC=1020 units); phase dispatch @0x02EC:
            2 -> 0x4DE2+0x072B (connect)   4 -> 0x0377 dial counter
            7/8 -> 0x073C                  default -> 0x0377
        0x0377: r6:r7=0x00AC ; LCALL 0x8054 (EEPROM-backed counter read) ;
        INC A ; store 0x00AC ; if ==4 -> SETB bit7 of 0x077E, JMP 0x0617
        (seize line); else commit, LCALL 0x1007, LCALL 0x4DE2.
        """
        if not self.card_ok and self.phase in (7, 8):
            self.hang_up_release_line()
            return
        self.phase = 4                                  # dialing
        digits = self.dial_buffer()
        if len(digits) >= 4:
            self.seize_line_and_dial(digits)

    def dial_buffer(self) -> str:
        """XDATA 0x0783-0x078D: 11-byte dialed-digit buffer (cleared 0x3ED4)."""
        raw = self.mem.xdata[0x0783:0x078E]
        return "".join(chr(0x30 + d) for d in raw if 1 <= d <= 9)

    def seize_line_and_dial(self, digits: str) -> None:
        """0x0617-0x0718: line-seize pulse train on latch 0x8060 via image 0x2D:
            0x2D.0=1 -> [0x8060], delay 0x0FA0 (4000 units)
            0x2D.0=0 -> [0x8060], delay 0x0514 (1300)
            0x2D.0=1 -> [0x8060], delay 0x01F4 (500)
        then state=2, tone profile 2, phases 3/3, relays kept in 0x06B0&0x09,
        clock/flags committed through 0x7E1E.
        """
        m = self.mem
        m.xdata[0x8060] |= 0x01; self.timers.wait_units(4000)
        m.xdata[0x8060] &= 0xFE; self.timers.wait_units(1300)
        m.xdata[0x8060] |= 0x01; self.timers.wait_units(500)
        m.xdata[0x0803] = m.xdata[0x0089]               # config shadow refresh
        self.set_tone_profile(2)
        self.phase = 3
        self.call_state = 2
        m.xdata[0x06B0] &= 0x09

    def state_settle(self) -> None:
        """0x0401: phase>=7 -> 0x073C else 0x4DE2+0x072B (end-wait)."""
        if self.phase >= 7:
            self.hang_up_release_line()
        else:
            self.enter_call_end_wait()

    def _render_call_lcd(self) -> None:
        """Connected display (firmware line buffers 0x0708/0x0728 via 0x415D/0x7A2B):
        line1 = 'EM CHAMADA MM:SS', line2 = digits + credit counter.
        The real unit keeps the remaining-unit counter visible (0x768D digit
        refresh) and blinks 'RECARREGUE' style warnings near zero credit."""
        mm, ss = self.clock_min, self.clock_sec
        self.lcd.instr(0x01)                              # clear
        self.lcd.ddram = 0x80
        for ch in f"EM CHAMADA {mm:02d}:{ss:02d}  UNID:{self.credit_units:02d}":
            self.lcd.put_data(ord(ch))
        self.lcd.instr(0xC0)
        self.lcd.ddram = 0xC0
        if self.credit_units <= 1:
            for ch in "FAVOR DESLIGAR":
                self.lcd.put_data(ord(ch))
        else:
            for ch in self.dial_buffer():
                self.lcd.put_data(ord(ch))

    def state_hangup(self) -> None:
        """0x0423: LJMP 0x073C."""
        self.hang_up_release_line()

    def enter_call_end_wait(self) -> None:
        """0x072B: 0x30<-3 ; TMOD<-0x21 ; IP<-0x10 ; IE<-0x83 ; SETB 0x26.6
        ; LJMP 0x46C6 (LCD end-of-call record + 'FAVOR DESLIGAR' path)."""
        self.mem.xdata[0x1F] = 3

    def hang_up_release_line(self) -> None:
        """0x073C: 0x30<-2 ; TMOD<-0x21 ; IP<-0x10 ; IE<-0x93 (EA+ES+ET0+EX0)
        ; SETB 0x26.7 ; SETB 0x26.6 ; XDATA[0x0802]<-1 (release line latch)
        ; LJMP 0x5F3E (sets phase 8 'out of service' or 0x21 'line fault');
        then 0x0450 teardown: state->0, clock cleared, line latches reset."""
        self.mem.xdata[0x0802] = 1
        self.phase = 8
        self.call_state = 0              # 0x0450: call over, machine returns to idle
        self.clock_sec = self.clock_min = 0
        self._sec_divider = 0
        self.mem.xdata[0x0783:0x078E] = bytes(11)   # 0x3ED4 clear_dial_buffer

    def teardown_and_reinit(self) -> None:
        """0x0450: SP/PSW reset, 0x076E<-0, 0x2C|0x0F&~0x80 -> latch 0x8040,
        P1<-0xCF, zero IRAM counter set (0x0D28), IE<-0x82, SETB TR0,
        LCALL 0x95F3 (ext reset) ; accumulate metering: 16-bit add of
        [0x07B7]&3:[0x07B8] into [0x079E:0x079F] when 0x0782!=0 && 0x077F.4 ;
        'RETIRE O CARTAO' display when 0x21.2 ; poll 0x80C0 until quiet."""
        self.call_state = 0

    # -- metering pulses (T1 counter mode, 0x0755 tail) ----------------------------
    def on_meter_pulse(self) -> None:
        """0x0755 abort/reset path arms T1 as external counter (TMOD=0x51):
        each metering pulse on T1 pin adds one prepaid unit to the card pool;
        0x0CCA/0x0C83 divide by 6 (0x0771) before bumping the 16-bit unit
        counters 0x009E/0x0098 and set accounting-dirty 0x2B.5
        (log record via 0x6906+0x8238)."""
        self.credit_units += 1           # one tariff step purchased

    def _burn_unit(self) -> None:
        """Tariff step (0x3B20 credit poll): consume one prepaid unit; if the
        card runs dry, force hang-up (phase 7 -> 0x073C path)."""
        if self.credit_units > 0:
            self.credit_units -= 1
        self.units += 1                  # total metered units (0x009E counter)
        self.mem.xdata[0x009E] = self.units & 0xFF
        self.mem.xdata[0x009F] = (self.units >> 8) & 0xFF
        self.mem.xdata[0x2B] |= 0x20                    # SETB 0x2B.5 (log dirty)
        if self.credit_units == 0:
            self.phase = 7               # "FAVOR DESLIGAR" -> hang_up_release_line

    # -- tariff download (CMT session) ----------------------------------------------
    def arm_cmt_download(self) -> None:
        """0x103E: XDATA[0x0005]=0x46 'F' ; [0x0006]=0x17 ; copy ROM string
        0x108E 'CMTDOWNL\\0' to 0x0007 (0x7C2C) ; persist 10-byte record 0x7E1E
        so an interrupted download survives power loss."""
        self.mem.xdata[0x0005:0x0010] = b"F\x17CMTDOWNL\x00"
        self.store.commit(0x0005, 10)

    def cmt_download_armed(self) -> bool:
        """0x1064: C = ([0x0005]==0x46) && ([0x0006]==0x17) &&
        memcmp(0x0007, ROM 'CMTDOWNL', 8)==0.  Checked at boot resume 0x01A1."""
        return self.mem.xdata[0x0005:0x000F] == b"F\x17CMTDOWNL"

    # -- supervisor console -----------------------------------------------------------
    def step(self) -> None:
        """One main-loop iteration = dispatch + per-tick services."""
        self.tick_call_clock()
        self.dispatch()
        self._render_state_lcd()

    def _render_state_lcd(self) -> None:
        if self.call_state in (1, 2):
            self._render_call_lcd()


# ============================================================================
# Static data tables (address-verified in the image)
# ============================================================================

def load_tables(path: str) -> dict:
    """Extract address-keyed data tables from the binary."""
    data = open(path, "rb").read()
    return {
        # 0x8AEE: tone/relay pattern table (indexed by 0x0D87 argument)
        "tone_table_8AEE": data[0x8AEE:0x8B05],
        # 0x8AC4: 5-byte per-profile records ('record len' byte 0x05 at 0x8AC3)
        "profile_records_8AC4": data[0x8AC4:0x8AEE],
        # 0x108E: download session tag
        "cmt_tag_108E": data[0x108E:0x1097],
        # 0x760B: LCD controller programming payload (7 bytes)
        "lcd_payload_760B": data[0x760B:0x7612],
        # 0x8AD9/0x8ADE/0x8AE8: message-pointer tables {count, records 19B}
        "msg_tables": {"T30": data[0x8AD9:], "T99": data[0x8ADE:], "T33": data[0x8AE8:]},
        # strings: plain ASCII runs (see full list in icatel_4317_strings.txt)
        "semint_banner_8C66": data[0x8C66:0x8C70],
    }


TICK_RELOAD = 0xFC47          # Timer0 reload -> 953 cycles -> ~1.034 ms tick
UART_RELOADS = {              # T1 pairs; see Serial docstring
    "comm_default": (0xF9, 0x2D),
    "charge_p1":    (0xFF, 0x1D),
    "charge_p2":    (0xFA, 0x10),
    "supervisor":   (0xF7, None),
}

if __name__ == "__main__":
    ph = Payphone()
    ph.reset()
    assert ph.cmt_download_armed() is False
    ph.arm_cmt_download()
    assert ph.cmt_download_armed() is True
    ph.set_tone_profile(1)                      # dial tone profile
    for _ in range(90):
        ph.step()                               # 1.5 simulated minutes idle
    ph.card_ok = True
    ph.mem.xdata[0x0075] = 1                    # hook event pending (0x0DD3)
    ph.call_state = 1
    ph.step()
    print(ph.lcd.text())
    print("OK: mailbox=%02X tone=%d phase=%d" % (ph.mail.read(), ph.tone_profile, ph.phase))
