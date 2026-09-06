# ICATEL / TPCI Payphone — Firmware Reverse Engineering, Python Rewrite & Simulator

*[Leia em português](docs/pt-br/README.md)*

Reverse engineering of `Icatel-43.17.bin` — the 8051 firmware of a 2004 Brazilian
card payphone (TPCI, *Telefone Público a Cartão Indutivo* — the official TELEBRÁS
term, see [Official terminology](#official-terminology-telebrás-standards) below —
by ICATEL) — plus a faithful Python re-implementation, a didactic simplified
engine, and a tkinter desktop simulator that looks like the real orelhão.

All analysis was done with **radare2** (`r2 -a 8051`) the full disassembly is saved in [Icatel-43.17.r2.asm](Icatel-43.17.r2.asm).

---

## Files

| File | What it is |
|---|---|
| `Icatel-43.17.bin` | Original 64 KB firmware dump (ground truth) |
| `icatel_4317_reimplementation.py` | Faithful annotated Python rewrite of the firmware |
| `simple_payphone.py` | Simplified, didactic engine (same observable behavior) |
| `icatel_4317_simulator.py` | Tkinter+ttk payphone simulator (GUI shell) |
| `Icatel-43.17.r2.asm` | Full radare2 linear disassembly (46 020 lines) |
| `Icatel-43.17.r2.functions.txt` | radare2 function list (`afl`) |
| `icatel_4317_strings.txt` | Portuguese LCD string table with verified addresses |

---

## Official terminology (TELEBRÁS standards)

Naming in this project follows, where possible, the TELEBRÁS engineering
standards for card payphone support:

| Term | Official meaning | Standard |
|---|---|---|
| **TPCI** | *Telefone Público a Cartão Indutivo* (card payphone) | 245-300-707 |
| **CSA** | *Centro de Supervisão Automatizada* (automated supervision center, the remote billing/fault backend) | 245-300-701 / 245-300-709 |
| **UT** | *Unidade de Tarifação* — the value, in local currency, of one charge pulse; equivalent to one credit | 245-300-707 |
| **Junto de Entrada** | Exchange-side equipment (or the terminal itself) that emits the charge pulse to the TPCI | 245-300-707 |
| **Método de Cobrança** | Billing method: the phone debits one card UT *first*, then grants the corresponding talk time | 245-300-707 |
| **CAT** | *Comando de Atualização da Tabela* (tariff-table update command, CSA↔TPCI protocol) | 245-300-709 |

In the code and docs, "UT" is reserved for the card's tariff credit
(`credit_units` / `eng.units`), to avoid confusion with the firmware's
internal "wait units" — the `0x7B6A` timing-loop ticks (see
`Timers.wait_units`), which have nothing to do with billing.

### Hardware cross-reference: the UCI/100 board

TELEBRÁS standard 560-400-301 (installation procedures) defines **UCI/100**
as "the TPCI's card control board" — almost certainly the board carrying the
8051 this project reverse-engineers. Its Annex II is a layout drawing of
that board's connectors and configuration jumpers, redrawn here in ASCII
(component positions approximate, values verbatim):

```
+----------------------------------------------------------+
|[9]                                                        |
|[8]                                        o           o   |
|[7]                                                        |
|[6]                          +------------------------+    |
|[5]  <- CN4 (lines A/B)      |  MODEM POWER JUMPERS    |    |
|[4]     terminal block       |   (ST4)            ST1  |    |
|[3]                          |   o--o--o--o            |    |
|[2]                          |   1  2  3  4            |    |
|[1]                          +------------------------+    |
|                                                            |
|                 [ST3]                                     |
|                                                            |
|                                [ST2-A]     [ST2-B]         |
|                                                            |
|                        (CH1) <- internal button            |
|                                                            |
|   o                                              o         |
|[ST5]                                                       |
+------------------------------------------+                |
                                            | (mechanical     |
                                            |  cutout)        |
                                            +-----------------+
```

* **CN4** — the A/B telephone-line terminal block (9 pins).
* **ST4** (jumper, positions 1-4) — modem transmit power: ST4-A −9 dBm,
  ST4-B −12 dBm, ST4-C −15 dBm, ST4-D −18 dBm, ST4-E −21 dBm.
* **ST3** — dialing mode: jumper present = DTMF, removed = decadic (pulse).
* **ST1** + **ST2-A**/**ST2-B** — charge-pulse signaling select: 12 kHz tone
  vs. line-polarity inversion.
* **CH1** — the internal configuration button; pressing it while lifting the
  handset opens the Counter (UT) / Test / Installation menu described in the
  standard's flowchart annex.

None of this is visible from the firmware image alone — it is a genuine
cross-check from the official documentation, not an `[INFERENCE]`.

### Cross-reference: old personal research notes

I did my own hardware/firmware research on a *related but different* ICATEL
unit (model 5000c/1) about 22 years ago, when I had physical access to a
phone and a firmware dump I'd labeled version "46.17" — not this project's
`43.17`. Several of those old software-level notes check out byte-for-byte
against `icatel_4317_strings.txt`, which is worth recording since it
corroborates my own old observation that ICATEL units are "95%+ similar"
across models/firmware revisions:

* **Self-test order matches exactly.** I'd noted a boot self-test sequence
  "eeprom, ram, teclado, display, matriz (unidentified), tabela E2P, leitora
  de cartões, modem", each returning an OK/failure message.
  `icatel_4317_strings.txt` has the identical sequence, in the same order,
  with matching OK/failure string pairs (`TESTANDO EEPROM`/`EEPROM
  OK`/`FALHA EEPROM` … `TESTANDO MATRIZ`/`MATRIZ OK`/`FALHA NA MATRIZ` …
  `TESTANDO E2P TAB` … `TESTANDO LEITORA` … `TESTANDO MODEM`). I couldn't
  identify what "matriz" tests back then; this image confirms the stage is
  real but still doesn't resolve its target — most likely the keypad's
  row/column scan matrix, distinct from the `TECLADO`/`TECLE` keypress
  check, but that's an `[INFERENCE]`, not a byte-verified fact.
* **Technician menu strings match.** The RESET-button technician menu I'd
  documented (`ID TECNICO`, `TAB.TARIFACAO`, `F.TARIFACAO`/`AUTOTARIFADO`,
  `NUMERO SERIE`, `TERMINAL SSTP`, `TERMINAL TPCI`, `ATIVACAO`/`DESATIVACAO`)
  lines up with real strings in this image: `IDENT.TÉCNICO`, `TÉCNICO
  INVÁLIDO`, `TAB.TARIFAÇÃO`, `F.TARIFAÇÃO`, `AUTOTARIFADO`, `NUMERO SÉRIE`,
  `TERMINAL SSTP`, `TERMINAL TPCI`, `DESATIVAÇÃO OK`, `INSTALAÇÃO OK`.
* **The phone calls the CSA "SSTP" internally.** Every reference to the
  supervision backend in the LCD strings says `SSTP`/`SSTP OCUPADO`, never
  `CSA` — worth knowing since this repo (and the TELEBRÁS standards) use
  "CSA" throughout.
* **Billing-mode names exist as real strings**, matching the 4-value
  tariff-mode set I'd recorded from the serial-number scheme: `DECADICA`,
  `DTMF`, `INVERSÃO`, `12 KHz`, `AUTO-DDD`, `AUTOTARIFADO`. This image also
  has a `16 KHz` string I hadn't seen before — a revision difference, not a
  contradiction.
* **The door-open message is real**: `PORTA ABERTA` is a verbatim string
  here, matching what I'd noted back then (opening the door with the lock
  still engaged displays that message) — and confirming there is *no*
  3-minute alarm timer, contrary to a "myth" I'd already debunked at the
  time.
* **A wording gap versus the official standard**: TELEBRÁS 245-300-707
  §8.21(i) mandates the exact LCD text `FORA DE SERVIÇO` for the
  out-of-service state; this firmware's actual string is `FORA DE OPERAÇÃO`
  — a real (harmless) deviation from the standard's letter.
* **Hardware difference, not a match**: I'd recorded a 2×16 LCD (Solomon,
  no backlight) on the 5000c/1; this project's own byte-verified DDRAM
  addressing (`0x80`/`0xC0`, 40-column line stride) makes the 43.17 unit's
  display 2×40 — the two ICATEL units differ here, they don't corroborate
  each other.

My old hardware teardown notes (specific EPROM/SRAM/modem/RTC part numbers,
crystal frequencies, PCB switch wiring) describe the *5000c/1*'s physical
board and aren't something a firmware byte dump can confirm or deny for the
*43.17* unit, so they're deliberately not reproduced here as fact.

---

## The firmware in one page

* **CPU**: 8051, 11.0592 MHz (inferred; standard UART crystal), 64 KB external space.
* **Vectors**: RST→`0x0033`, INT0→`0x41F7` (keyboard), Timer0→`0x1B9A`
  (~1.034 ms tick, reload `0xFC47`), Timer1→`0x2EF6` (charge-pulse phases),
  UART→`0x4264` (supervisor/charge protocol, CRC-16 with feedback `0x0240`).
* **Main loop** (`0x026B`): `call_state` (XDATA `0x076E`) × 3 indexes an `ljmp`
  table → idle / call-active / settle / hang-up handlers.
* **Persistence**: three additive-checksummed XDATA blocks (`0x0000`/25 B,
  `0x0019`/95 B, `0x0078`/24 B) mirrored into a 24C64-class I²C EEPROM
  (SCL=P3.3, SDA=P1.7, device `0xA0`/`0xA1`, 2-byte addressing, 64-byte pages).
  Writes go through `0x7E18/0x7E1E`, which verify the trailer and rewrite the
  EEPROM shadow on mismatch.
* **LCD**: 2×40 HD44780-class. Reset-time bit-bang on P1.4–P1.7, runtime
  memory-mapped window `0x8000` (instr) `0x8001` (data) `0x8002` (busy) `0x8003` (read).
* **Tariff metering**: T1 as external pulse counter (TMOD=`0x51`), divide-by-6
  (`0x0771`) before bumping 16-bit unit counters (`0x009E/0x009F`); credit pool
  polled by `0x3B20`; card runs dry → phase 7 → `FAVOR DESLIGAR` → hang-up.
* **Easter egg of the build**: `0x0DC9` (`PUSH ACC; MOV A,#1; RRC A; POP; RET`)
  returns carry=1 unconditionally — every feature-gate branch after it is dead
  code in this build.
* **Quirk**: the command mailbox at XDATA `0x0708` is mirrored to **port P1**
  on every write (`0x415D`), while P1.7 doubles as the I²C SDA pin — the board
  muxes the pin in hardware.

---

## `icatel_4317_reimplementation.py` — the faithful rewrite

A runnable Python model of the firmware. Every routine carries the real 8051
disassembly as a comment, then explains what it does. Examples:

```python
def tick_call_clock(self) -> None:
    """0x4DE2: advance 0x087F (sec) with mod-60 carry into 0x087E (min) ..."""
```

Structure:

* `Memory` — XDATA + memory-mapped I/O model (LCD window, latches, PCON idle).
* `Timers` / `timer0_isr` — the 953-cycle tick and the `0x26.3` flag contract.
* `I2cEeprom` — bit-banged 24Cxx driver (`0x8050` read byte, `0x843E` read block,
  `0x85F4` page write with tWr ack-poll burst).
* `Datastore` — the protected-variable system (`0x7D56` block select,
  `0x7D9D` additive checksum, `0x7DAF` verify, `0x7DC3` EEPROM rewrite).
* `Mailbox` — the `0x0708` command byte + P1 mirror + the `0x40EA` LFSR mixer.
* `Lcd` — both LCD interfaces, DDRAM cursor model, custom accent charset.
* `Serial` — the `0x4264` frame protocol (9 start codes, CRC-16, REN flow control).
* `Payphone` — the state machine: `reset()`, `dispatch()`, state handlers,
  `set_tone_profile()` (`0x0D87`), `seize_line_and_dial()` (`0x0617`),
  `tick_call_clock()` (`0x4DE2` + metering), `hang_up_release_line()` (`0x073C`),
  CMT download arm/check (`0x103E`/`0x1064`).

Run it standalone for a smoke test:

```bash
python3 icatel_4317_reimplementation.py
```

### Billing model (what the simulator drives)

| Concept | Firmware | Python |
|---|---|---|
| Card credit pool | polled by `0x3B20` | `eng.credit_units` |
| Metered-units counter | `0x009E/0x009F` | `eng.units` |
| Tariff step | div-6 `0x0771` in T1 ISR | 1 unit / 6 s while connected |
| Card empty | phase 7 → `0x073C` | `_burn_unit()` forces hang-up |

---

## `simple_payphone.py` — the didactic engine

Same observable behavior, one readable state machine:

```
IDLE ──off-hook──> DIAL_TONE ──4+ digits──> CONNECTED ──credit==0 / on-hook──> IDLE
```

* No EEPROM, no CRC, no banked memory — just the state machine, the digit
  buffer (11 max, `*` clears), the per-second clock (mod-60) and the
  1-unit-per-6-seconds tariff.
* Phase codes mirror the firmware (`2` waiting, `3` connected, `4` dialing,
  `7` hang-up, `8` out of service).
* Self-testing: `python3 simple_payphone.py` runs a headless assertion script
  (off-hook → dial → connect → paid call sustains → hang-up → out-of-credit drop).

---

## `icatel_4317_simulator.py` — the simulator

Tkinter + ttk desktop app styled after the Tropical/orelhão shell:
steel-blue body, yellow stripe, green 2×40 LCD, metal 4×3 keypad with orange
`*`/`#`, a clickable ICATEL card sliding into the slot, a clickable handset,
and hook/credit buttons.

### Usage

```bash
# default: faithful engine (icatel_4317_reimplementation)
python3 icatel_4317_simulator.py

# didactic engine
python3 icatel_4317_simulator.py --engine simple
```

### Controls

| Control | Effect |
|---|---|
| Click handset / "Tirar do gancho" | Off-hook (starts the flow) |
| Click the card (or the slot area) | Insert / remove the phonecard |
| Keypad `0-9` | Dial (buffer at XDATA `0x0783`, max 11 digits) |
| Keypad `*` | Clear the dialed buffer (firmware `0x3ED4`) |
| "+1 crédito" | One prepaid unit onto the card (metering pulse on T1) |
| "Colocar no gancho" / Esc | On-hook (ends the call, `0x073C` teardown) |

Status bar shows live engine state:
`GANCHO | CARTÃO | CRÉDITO: n unid | GASTO: n | fase | estado`
(phase/estado mirror firmware `0x0013`/`0x076E`).

### A full call

1. Insert the card (click it). Off-hook.
   * **No credit** → LCD shows `CARTAO SEM CREDITO` / `FORA DE OPERACAO`; the
     line is never seized (firmware checks card value before dial-out).
2. Press **+1 crédito** a couple of times → dial tone state.
3. Dial ≥ 4 digits (`*` corrects) → line-seize pulse train on latch `0x8060`
   (4000/1300/500 units, exactly like `0x0617`) → `EM CHAMADA MM:SS  UNID:nn`
   with the dialed number on line 2.
4. Talk: 1 unit burns every 6 seconds. The remaining-unit counter updates live
   (like the real unit's digit refresh `0x768D`).
5. Credit near zero → `FAVOR DESLIGAR` on line 2; at zero the call is dropped
   and the phone returns to idle. On-hook works at any time.

Dependencies: Python ≥ 3.9 with tkinter (standard on macOS/Windows; on
Linux `sudo apt install python3-tk`). No third-party packages.

---

## How the RE was done (method)

1. **Structure** — `r2 -a 8051` auto-analysis, vector table decoding, byte-level
   cross-checks of every quoted instruction.
2. **Parallel scouting** — four read-only agents mapped disjoint regions
   (main loop / serial+download / display / EEPROM+checksums), each returning
   byte-verified facts with `[INFERENCE]` markers.
3. **Cross-reference** — the sibling `Tp2k_dump.src` (DIS8051 listing of a
   *different* build, 37 693 bytes differ) used only to corroborate code shape.
4. **Rewrite** — every quoted asm snippet in the Python was byte-checked against
   the image (`75 8C FC/75 8A 47` @`0x1B9A`, `SETB 0x26.3` @`0x1ECD`, ljmp
   table @`0x0277`, strings at `0x8AC4`+ …).

## Known limitations

* `simple_payphone.py` intentionally drops the EEPROM/protocol/banked layers.
* The faithful engine models the state machine, datastore, I²C and LCD
  contracts, but not cycle-exact timing or the analog line interface.
* Crystal (11.0592 MHz) and nominal baud (9600 from TH1=`0xFA`) are `[INFERENCE]`
  — the only standard-rate reload value in the image.

---

## License

The Python code in this repository (`icatel_4317_reimplementation.py`,
`simple_payphone.py`, `icatel_4317_simulator.py`) is released under the
[MIT License](LICENSE). This does **not** cover `Icatel-43.17.bin` itself
(the original firmware dump, quoted here only for analysis) or the
disassembly/strings files derived byte-for-byte from it — those remain the
property of ICATEL / the original manufacturer.
