#!/usr/bin/env python3
"""
TPCI / ICATEL (2004) Brazilian card payphone -- desktop simulator.

tkinter + ttk.  Looks like the real phone: steel-blue body, handset that can
be lifted (hook), 2x40 LCD, 4x3 metal keypad, card slot with a clickable
phonecard, and the orange "correction/redial" key.

Two engines, selectable with ENGINE:
    "full"  -> icatel_4317_reimplementation.Payphone  (faithful RE model)
    "simple" -> simple_payphone.PayphoneSim           (didactic rewrite)

Run:  python3 icatel_4317_simulator.py [--engine simple]
"""

from __future__ import annotations
import sys
import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------------------
# engine selection
# ---------------------------------------------------------------------------
ENGINE = "full"
if "--engine" in sys.argv:
    ENGINE = sys.argv[sys.argv.index("--engine") + 1]

FULL = None
SIMPLE = None
if ENGINE == "full":
    import icatel_4317_reimplementation as full_mod
    FULL = full_mod
else:
    import simple_payphone as simple_mod
    SIMPLE = simple_mod

# ---------------------------------------------------------------------------
# visual constants (Tropical "orelhao" colors)
# ---------------------------------------------------------------------------
C_BODY      = "#1d4f8c"   # deep steel blue (Tropical shell)
C_BODY_DARK = "#123257"
C_BODY_LT   = "#2a6cb4"
C_LCD_FACE  = "#7d8a68"   # olive-gray LCD bezel
C_LCD_ON    = "#9aad3c"   # greenish STN backlight
C_LCD_OFF   = "#6d7a30"
C_METAL     = "#b9bec4"
C_METAL_D   = "#7c8288"
C_KEY_FACE  = "#d7dade"
C_KEY_TXT   = "#1c1f22"
C_ORANGE    = "#e07b1f"
C_SLOT      = "#0c0c10"
C_HANDSET   = "#14202e"
C_ACCENT    = "#e8b93c"   # yellow TPCI stripe

KEY_ROWS = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"],
]

LCD_COLS = 40

class PayphoneSim(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("TPCI / ICATEL - Terminal Publico de Chamadas (2004) - simulador")
        self.resizable(False, False)
        self.configure(bg=C_BODY_DARK)
        self._exiting = False

        # engine ------------------------------------------------------------
        self.hook_off = False
        self.card_inserted = False
        self.credits = 0.0
        if FULL:
            self.eng = FULL.Payphone()
            self.eng.reset()
        else:
            self.eng = SIMPLE.PayphoneSim()

        self._build_ui()

        # periodic engine tick (20 Hz -- firmware tick is ~1 ms, we scale)
        self.after(50, self._on_tick)

    def destroy(self) -> None:               # stop the tick loop before teardown
        self._exiting = True
        super().destroy()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        # ---- handset (top) -------------------------------------------------
        hs = tk.Canvas(self, width=640, height=88, bg=C_BODY_DARK, highlightthickness=0)
        hs.pack()
        self._draw_handset(hs)

        # ---- body ----------------------------------------------------------
        body = tk.Frame(self, bg=C_BODY, padx=18, pady=14,
                        highlightbackground=C_BODY_DARK, highlightthickness=2)
        body.pack(fill="both", expand=True)

        stripe = tk.Frame(body, bg=C_ACCENT, height=6)
        stripe.pack(fill="x", pady=(0, 10))
        tk.Label(body, text="ICATEL  •  TERMINAL TPCI", bg=C_BODY, fg="white",
                 font=("Helvetica", 11, "bold")).pack(anchor="w")

        # ---- LCD -----------------------------------------------------------
        lcd_frame = tk.Frame(body, bg=C_LCD_FACE, bd=0, padx=10, pady=8)
        lcd_frame.pack(fill="x")
        self.lcd_lines = [
            tk.Label(lcd_frame, text=" " * LCD_COLS, bg=C_LCD_ON, fg="#10160a",
                     font=("Courier", 15, "bold"), anchor="w", width=LCD_COLS)
            for _ in range(2)
        ]
        for w in self.lcd_lines:
            w.pack(fill="x", pady=1, ipady=2)

        # ---- middle row: keypad + card slot --------------------------------
        mid = tk.Frame(body, bg=C_BODY)
        mid.pack(fill="x", pady=10)
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=0)

        pad = tk.Frame(mid, bg=C_BODY)
        pad.grid(row=0, column=0, sticky="n")
        self._build_keypad(pad)

        slot = tk.Frame(mid, bg=C_BODY)
        slot.grid(row=0, column=1, sticky="ne", padx=(16, 0))

        # card slot drawing + the card itself (clickable)
        self.card_canvas = tk.Canvas(slot, width=120, height=170, bg=C_BODY,
                                     highlightthickness=0)
        self.card_canvas.pack()
        self._draw_slot(self.card_canvas)
        self._draw_card(self.card_canvas, inserted=False)
        self.card_canvas.bind("<Button-1>", self._toggle_card)

        tk.Label(slot, text="clique = inserir/remover cartão",
                 bg=C_BODY, fg="#cfe0ff", font=("Helvetica", 8)).pack()

        # ---- bottom controls ------------------------------------------------
        bottom = ttk.Frame(body)
        bottom.pack(fill="x", pady=(8, 0))
        self.hook_btn = ttk.Button(bottom, text="📂  Tirar do gancho (off-hook)",
                                   command=self._toggle_hook)
        self.hook_btn.pack(side="left")
        ttk.Button(bottom, text="💰 +1 crédito", command=self._add_credit)\
            .pack(side="left", padx=8)
        ttk.Button(bottom, text="🔌 Colocar no gancho", command=self._on_hook)\
            .pack(side="left")
        self.status = ttk.Label(bottom, text="GANCHO: no gancho | CARTÃO: ausente | CRÉDITO: R$ 0.00")
        self.status.pack(side="right")

        self.bind("<Escape>", lambda e: self.destroy())

    def _draw_handset(self, cv: tk.Canvas) -> None:
        # coiled cord
        x = 40
        for i in range(12):
            cv.create_oval(x, 62, x + 14, 82, outline="#5a6570", width=2)
            x += 12
        self.handset = cv.create_rectangle(150, 18, 470, 62, fill=C_HANDSET,
                                           outline="#0a121c", width=2)
        cv.create_oval(160, 22, 230, 58, fill=C_HANDSET, outline="#0a121c")
        cv.create_oval(390, 22, 460, 58, fill=C_HANDSET, outline="#0a121c")
        self.hook_y = 0
        cv.tag_bind(self.handset, "<Button-1>", lambda e: self._toggle_hook())

    def _build_keypad(self, parent: tk.Frame) -> None:
        wrap = tk.Frame(parent, bg=C_METAL_D, padx=10, pady=10, bd=0)
        wrap.pack()
        self.keys: dict[str, ttk.Button] = {}
        orange = {"*", "#"}
        for r, row in enumerate(KEY_ROWS):
            for c, k in enumerate(row):
                color = C_ORANGE if k in orange else C_KEY_FACE
                fg = "white" if k in orange else C_KEY_TXT
                b = tk.Button(wrap, text=k, width=4, height=2,
                              bg=color, fg=fg, activebackground=C_METAL,
                              activeforeground="black", relief="raised", bd=2,
                              font=("Helvetica", 13, "bold"),
                              command=lambda ch=k: self._key(ch))
                b.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")

    def _draw_slot(self, cv: tk.Canvas) -> None:
        cv.create_rectangle(28, 6, 92, 26, fill=C_SLOT, outline="#000")
        cv.create_text(60, 16, text="CARTÃO", fill="#666", font=("Helvetica", 7))
        cv.create_rectangle(10, 40, 110, 150, outline=C_METAL_D, width=2)
        # little "insira nesta direção" arrow
        cv.create_polygon(60, 152, 52, 142, 68, 142, fill="#cfd6dd")

    def _draw_card(self, cv: tk.Canvas, inserted: bool) -> None:
        cv.delete("card")
        if inserted:
            y0 = 60
        else:
            y0 = 108            # sticking out of the slot
        cv.create_rectangle(20, y0, 100, y0 + 60, fill="#e8e4d8",
                            outline="#8a8778", width=2, tags="card")
        cv.create_rectangle(24, y0 + 6, 44, y0 + 22, fill="#c9a227",
                            outline="#7a611a", tags="card")   # gold chip
        cv.create_text(62, y0 + 40, text="ICATEL", font=("Helvetica", 8, "bold"),
                       fill="#1d4f8c", tags="card")

    # ------------------------------------------------------------- events --
    def _toggle_card(self, _e=None) -> None:
        self.card_inserted = not self.card_inserted
        self._draw_card(self.card_canvas, self.card_inserted)
        if FULL:
            self.eng.card_ok = self.card_inserted
        else:
            self.eng.card_inserted(self.card_inserted)
        if self.card_inserted:
            self._beep()
        self._refresh_status()

    def _toggle_hook(self) -> None:
        self.hook_off = not self.hook_off
        if FULL:
            # firmware: 0x0DD3 tests XDATA[0x0075] != 0 (hook event pending)
            self.eng.mem.xdata[0x0075] = 1 if self.hook_off else 0
            if not self.hook_off and self.eng.call_state in (1, 2, 3):
                self.eng.phase = 7
        else:
            self.eng.hook(self.hook_off)
        self._beep()
        self._refresh_status()

    def _on_hook(self) -> None:
        if self.hook_off:
            self._toggle_hook()

    def _add_credit(self) -> None:
        if FULL:
            self.eng.on_meter_pulse()    # one prepaid unit onto the card
        else:
            self.eng.credit_unit()
        self._beep()
        self._refresh_status()

    def _key(self, ch: str) -> None:
        if FULL:
            # firmware digits land in buffer 0x0783..0x078D (0x3ED4 clears it)
            buf = self.eng.mem.xdata[0x0783:0x078E]
            idx = next((i for i, b in enumerate(buf) if b in (0, 0xFF)), None)
            if idx is not None and ch.isdigit():
                self.eng.mem.xdata[0x0783 + idx] = int(ch)
            elif ch == "*":                       # correction: clear buffer
                for i in range(11):
                    self.eng.mem.xdata[0x0783 + i] = 0
            self.eng.card_ok = self.card_inserted
        else:
            self.eng.keypress(ch)
        self._beep()
        self._refresh_status()

    def _beep(self) -> None:
        # 1 kHz, 60 ms (DTMF-ish tick; the real box uses the line for audio)
        try:
            self.bell()
        except Exception:
            pass

    # --------------------------------------------------------------- tick --
    def _on_tick(self) -> None:
        if self._exiting:
            return
        if FULL:
            eng = self.eng
            eng.card_ok = self.card_inserted
            if self.hook_off and self.card_inserted and eng.call_state == 0:
                eng.mem.xdata[0x0075] = 1
                # firmware checks card value before seizing the line (0x3B20)
                if eng.credit_units > 0:
                    eng.call_state = 1
                else:
                    eng.lcd.instr(0xC0)
                    eng.lcd.ddram = 0xC0
                    for ch in "CARTAO SEM CREDITO":
                        eng.lcd.put_data(ord(ch))
            if not self.hook_off and eng.call_state in (1, 2, 3):
                eng.phase = 7
            eng.step()
            l1, l2 = eng.lcd.text().split("\n")
            self._set_lcd(l1, l2)
        else:
            self.eng.tick()
            self._set_lcd(self.eng.lcd_line1(), self.eng.lcd_line2())
        self._refresh_status()
        self.after(50, self._on_tick)

    # -------------------------------------------------------------- misc --
    def _set_lcd(self, l1: str, l2: str) -> None:
        self.lcd_lines[0].config(text=(l1 or "").center(LCD_COLS)[:LCD_COLS])
        self.lcd_lines[1].config(text=(l2 or "").center(LCD_COLS)[:LCD_COLS])

    def _refresh_status(self) -> None:
        if FULL:
            units = self.eng.units
            credit = self.eng.credit_units
            phase = self.eng.phase
            state = self.eng.call_state
        else:
            units = self.eng.units
            credit = self.eng.units
            phase = self.eng.phase
            state = self.eng.state
        self.status.config(
            text=f"GANCHO: {'off-hook' if self.hook_off else 'no gancho'} | "
                 f"CARTÃO: {'inserido' if self.card_inserted else 'ausente'} | "
                 f"CRÉDITO: {credit} unid | GASTO: {units} | fase: {phase} | estado: {state}")


if __name__ == "__main__":
    app = PayphoneSim()
    app.mainloop()
