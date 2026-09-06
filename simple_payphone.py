#!/usr/bin/env python3
"""
simple_payphone.py -- simplified, didactic implementation of the ICATEL
TPCI payphone logic, written to run under the tkinter simulator
(icatel_4317_simulator.py --engine simple).

Same observable behavior as the RE'd firmware, one readable state machine:

    IDLE ──off-hook──> DIAL_TONE ──digits──> CONNECTED ──credit==0/hang-up──> FAULT/IDLE

Contract kept from the firmware:
  * 2x40 LCD, line1 = status, line2 = dynamic info ("FORA DE OPERACAO",
    "COLOQUE CARTAO", "DISQUE: 1234", timer, "FAVOR DESLIGAR").
  * Off-hook without card -> "COLOQUE CARTAO", with card -> dial tone state.
  * Digits buffer (11 max, '*' clears) mirrors XDATA 0x0783-0x078D.
  * 4+ digits -> CONNECTED, per-second timer (mod-60, like 0x4DE2/0x789C),
    each credit unit (metering pulse / T1 pin) decrements available units.
  * Out of credit -> "Favor desligar" + phase 7 -> hang-up, state 0.
  * Card removal mid-call -> immediate fault phase, hang-up.
"""

from __future__ import annotations
import time

LCD_COLS = 40

# phases mirror firmware 0x0013 values where possible
P_IDLE, P_WAITING, P_CONNECTED, P_DIALING, P_HANGUP, P_FAULT = 0, 2, 3, 4, 7, 8

class PayphoneSim:
    def __init__(self) -> None:
        # state
        self.state = 0            # 0 idle, 1 tone/dialing, 2 connected, 3 ended
        self.phase = P_IDLE       # 0x0013-ish
        self.card = False
        self.hook_off = False     # False = on hook
        self.units = 0            # available credit units
        self.digits = ""          # dialed buffer (max 11)
        self.connected_for = 0    # seconds in call
        self.msg_timer = 0        # ticks left for transient messages
        self.transient = ""       # transient line2 message
        self.tone = 0             # 0 none, 1 dial tone, 2 in-call
        # LCD shadow (0x0708-0x0747 in firmware)
        self._l1 = " " * LCD_COLS
        self._l2 = " " * LCD_COLS

    # ---------------------------------------------------------- hardware --
    def hook(self, off: bool) -> None:
        self.hook_off = off
        if not off:                          # on hook during call -> hang up
            if self.state in (1, 2):
                self._end_call("FAVOR DESLIGAR")

    def card_inserted(self, present: bool) -> None:
        was, self.card = self.card, present
        if not present and self.state == 2:
            self._end_call("CARTAO RETIRADO")     # firmware: 0x2F.4 path
        elif present and not was and self.state == 0:
            self._show("COLOQUE CARTAO OK")       # brief acknowledge

    def credit_unit(self) -> None:
        """One metering pulse (T1 pin in firmware TMOD=0x51 counter mode)."""
        self.units += 1
        if self.state == 2:
            self._render()

    def keypress(self, ch: str) -> None:
        if self.state != 1:
            return
        if ch == "*":                             # correction key clears buffer
            self.digits = ""
        elif ch.isdigit() and len(self.digits) < 11:
            self.digits += ch
        self._render()

    # ------------------------------------------------------------- engine --
    def tick(self) -> None:
        """Called at simulator rate (~20 Hz); 1 tick = 1 loop iteration."""
        # -- state machine first: transient messages only delay display, ----
        # -- never the off-hook / dial / connect transitions.
        if self.hook_off and self.card and self.state == 0:
            # firmware 0x01DC->0x020A: state=1, phases 4/4, tone profile 1
            self.state, self.phase, self.tone = 1, P_DIALING, 1
            self._render()
        elif self.hook_off and not self.card and self.state == 0 \
                and not self.transient:
            # firmware 0x0433: "COLOQUE CARTAO" display block
            self._show("COLOQUE CARTAO", ticks=40)
            self.phase = P_WAITING
        elif self.state == 1 and len(self.digits) >= 4:
            # firmware 0x0617: seize line, state=2, phases 3/3, tone 2
            self.state, self.phase, self.tone = 2, P_CONNECTED, 2
            self.connected_for = 0
            self.units = max(self.units, 1)       # first unit granted
            self._render()
        elif self.state == 2:
            self.connected_for += 1
            cost = self.connected_for // 6        # ~1 unit per 6 s
            if cost >= self.units:
                self._end_call("FAVOR DESLIGAR")
            else:
                self._render()
        # transient countdown last: display-only, never blocks transitions
        if self.msg_timer > 0:
            self.msg_timer -= 1
            if self.msg_timer == 0:
                self.transient = ""
                self._render()

    # -------------------------------------------------------------- misc --
    def _end_call(self, reason: str) -> None:
        self.state, self.phase, self.tone = 0, P_HANGUP, 0
        self.digits = ""
        self._show(reason + (" -> RETIRE O CARTAO" if self.card else ""),
                   ticks=60)
        if not self.card:
            self.phase = P_IDLE

    def _show(self, msg: str, ticks: int = 20) -> None:
        self.transient = msg
        self.msg_timer = ticks
        self._render()

    # --------------------------------------------------------------- LCD ---
    def _render(self) -> None:
        if self.transient:
            self._l1 = " " * LCD_COLS
            self._l2 = self.transient.center(LCD_COLS)[:LCD_COLS]
            return
        if self.state == 0:
            self._l1 = "   SEMINT 93 / ICATEL".ljust(LCD_COLS)[:LCD_COLS]
            self._l2 = "FORA DE OPERACAO".center(LCD_COLS)[:LCD_COLS]
        elif self.state == 1:
            self._l1 = "DISQUE (ate 11 digitos):"
            self._l2 = self.digits.ljust(LCD_COLS)[:LCD_COLS]
        elif self.state == 2:
            mm, ss = self.connected_for // 60, self.connected_for % 60
            self._l1 = f"EM CHAMADA  {mm:02d}:{ss:02d}  UNID:{self.units}"
            self._l2 = self.digits.ljust(LCD_COLS)[:LCD_COLS]
        else:
            self._l1, self._l2 = " " * LCD_COLS, " " * LCD_COLS

    def lcd_line1(self) -> str: return self._l1
    def lcd_line2(self) -> str: return self._l2


if __name__ == "__main__":
    # headless logic smoke test
    p = PayphoneSim()
    p.card_inserted(True); p.hook(True); p.tick()
    assert p.state == 1, p.state
    for ch in "1234": p.keypress(ch)
    p.tick()
    assert p.state == 2, p.state
    for _ in range(10): p.credit_unit()      # pay 10 units (card pulses)
    for _ in range(50): p.tick()
    assert p.state == 2 and p.connected_for >= 50
    p.hook(False)
    assert p.state == 0
    # out-of-credit path: default 1 unit -> drop after ~6 s (phase 7)
    p2 = PayphoneSim()
    p2.card_inserted(True); p2.hook(True); p2.tick()
    for ch in "1234":
        p2.keypress(ch)
    p2.tick()                                 # connect
    dropped = False
    for _ in range(12):
        p2.tick()
        if p2.state == 0 and p2.phase == 7 and p2.msg_timer > 0:
            dropped = True
            break
    assert dropped, (p2.state, p2.phase, p2.transient)
    print("simple engine smoke OK")
