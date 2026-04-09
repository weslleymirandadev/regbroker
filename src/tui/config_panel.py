"""Interactive full-screen configuration panel.

Modes
─────
  NAV    ↑↓ move cursor · Enter edit · s save+close · q/Esc close
  EDIT   type new value · Enter confirm · Esc cancel
  CHOICE ↑↓ or ←→ cycle options · Enter confirm · Esc cancel
"""
from __future__ import annotations

import shutil
from typing import Any, Optional

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style

try:
    from .. import config as cfg_mod
except ImportError:
    import config as cfg_mod

# ── Style ─────────────────────────────────────────────────────────────────────

PANEL_STYLE = Style.from_dict({
    "border":         "#3d4752",
    "title":          "#00afff bold",
    "section":        "#5fd7ff bold",
    "key":            "#8b949e",
    "cursor_key":     "#ffffff bold",
    "cursor_val":     "#00ff87",
    "val":            "#c9d1d9",
    "secret_val":     "#555577",
    "hint":           "#444455",
    "hint_key":       "#79c0ff",
    "edit_label":     "#ffa500 bold",
    "edit_input":     "#ffffff",
    "edit_cursor":    "bg:#0d3b66 #ffffff",
    "choice_active":  "#00ff87 bold",
    "choice_inactive":"#555577",
    "dirty":          "#f85149",
    "saved":          "#3fb950",
})

# ── Schema ────────────────────────────────────────────────────────────────────

SECTIONS: list[tuple[str, list[tuple[str, str, str, Any]]]] = [
    ("IDENTIDADE", [
        # (config_key, display_label, kind, options_or_None)
        # kind: text | secret | number | choice
        ("perito_name",   "Nome do perito",         "text",   None),
        ("perito_org",    "Órgão / Instituição",     "text",   None),
        ("perito_reg",    "Registro profissional",   "text",   None),
    ]),
    ("INTELIGÊNCIA ARTIFICIAL", [
        ("api_key",       "API Key (OpenRouter)",    "secret", None),
        ("model",         "Modelo",                  "text",   None),
        ("max_tokens",    "Max tokens",              "number", None),
        ("temperature",   "Temperature  (0.0–1.0)",  "number", None),
    ]),
    ("TIMESTAMPS", [
        ("ts_format",     "Formato de data/hora",    "choice", cfg_mod.TS_FORMATS),
        ("ts_custom_fmt", "Formato customizado",      "text",   None),
        ("timezone",      "Fuso horário",             "choice", cfg_mod.TIMEZONES),
    ]),
    ("EXPORTAÇÃO", [
        ("note_file",     "Arquivo de notas",         "text",   None),
    ]),
]

# Build flat index: cursor position → (section_label, key, label, kind, options)
def _build_flat() -> list[tuple[str, str, str, str, Any]]:
    out = []
    for sec, fields in SECTIONS:
        for key, label, kind, opts in fields:
            out.append((sec, key, label, kind, opts))
    return out

FLAT = _build_flat()


def _preview(fmt: str) -> str:
    """Show a live preview of what ISO → formatted looks like."""
    from datetime import datetime, timezone
    sample = datetime(2024, 3, 15, 14, 32, 7, tzinfo=timezone.utc)
    if fmt == "BR":     return sample.strftime("%d/%m/%Y %H:%M:%S")
    if fmt == "US":     return sample.strftime("%m/%d/%Y %H:%M:%S")
    if fmt == "UNIX":   return str(int(sample.timestamp()))
    if fmt == "custom": return "(use formato customizado)"
    return sample.strftime("%Y-%m-%d %H:%M:%S")


# ── Panel ─────────────────────────────────────────────────────────────────────

class ConfigPanel:
    """Open with .run(config_dict) — edits in-place, returns True if saved."""

    def __init__(self):
        self._cfg:      dict   = {}
        self._orig:     dict   = {}
        self._cursor:   int    = 0
        self._scroll:   int    = 0
        self._mode:     str    = "nav"       # nav | edit | choice
        self._edit_buf: Buffer = Buffer(multiline=False)
        self._choice_i: int    = 0           # index within options for choice mode
        self._dirty:    bool   = False
        self._saved:    bool   = False
        self._app:      Optional[Application] = None
        self._status:   str    = ""          # brief status message

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self, config: dict) -> bool:
        """Edit config in-place. Returns True if user saved."""
        self._cfg    = config
        self._orig   = dict(config)
        self._cursor = 0
        self._scroll = 0
        self._mode   = "nav"
        self._dirty  = False
        self._saved  = False
        self._status = ""

        is_nav    = Condition(lambda: self._mode == "nav")
        is_edit   = Condition(lambda: self._mode == "edit")
        is_choice = Condition(lambda: self._mode == "choice")
        is_input  = Condition(lambda: self._mode in ("edit", "choice"))

        layout = Layout(HSplit([
            # Main list area
            Window(
                content=FormattedTextControl(self._render_list, focusable=True),
                dont_extend_height=False,
            ),
            # Edit / choice area (shown when not in nav mode)
            ConditionalContainer(
                content=HSplit([
                    Window(height=1, content=FormattedTextControl(self._render_edit_header)),
                    Window(height=1, content=BufferControl(self._edit_buf,
                                                           focusable=True)),
                ]),
                filter=is_edit,
            ),
            ConditionalContainer(
                content=Window(height=1, content=FormattedTextControl(self._render_choice_bar)),
                filter=is_choice,
            ),
            # Footer
            Window(height=1, content=FormattedTextControl(self._render_footer)),
        ]))

        kb = KeyBindings()

        # ── NAV bindings ──────────────────────────────────────────────────────
        @kb.add("up",    filter=is_nav)
        @kb.add("k",     filter=is_nav)
        def _up(_):
            if self._cursor > 0:
                self._cursor -= 1
                self._clamp_scroll()
                self._app.invalidate()

        @kb.add("down",  filter=is_nav)
        @kb.add("j",     filter=is_nav)
        def _down(_):
            if self._cursor < len(FLAT) - 1:
                self._cursor += 1
                self._clamp_scroll()
                self._app.invalidate()

        @kb.add("enter", filter=is_nav)
        def _enter_nav(event):
            _, key, _, kind, opts = FLAT[self._cursor]
            if kind == "choice":
                cur_val = str(self._cfg.get(key, ""))
                try:
                    self._choice_i = opts.index(cur_val)
                except ValueError:
                    self._choice_i = 0
                self._mode = "choice"
            else:
                val = self._cfg.get(key, "")
                self._edit_buf.set_document(
                    Document(str(val), len(str(val))),
                    bypass_readonly=True,
                )
                self._mode = "edit"
                event.app.layout.focus(self._edit_buf)
            self._app.invalidate()

        @kb.add("s",     filter=is_nav)
        @kb.add("c-s",   filter=is_nav)
        def _save(event):
            cfg_mod.save(self._cfg)
            self._dirty  = False
            self._saved  = True
            self._status = "saved"
            event.app.exit()

        @kb.add("escape", filter=is_nav)
        @kb.add("q",      filter=is_nav)
        def _quit(event):
            if self._dirty:
                # Revert
                self._cfg.clear()
                self._cfg.update(self._orig)
            event.app.exit()

        @kb.add("c-c")
        def _cc(event):
            if self._dirty:
                self._cfg.clear()
                self._cfg.update(self._orig)
            event.app.exit()

        # ── EDIT bindings ─────────────────────────────────────────────────────
        @kb.add("enter",  filter=is_edit)
        def _enter_edit(event):
            _, key, _, kind, _ = FLAT[self._cursor]
            new_val = self._edit_buf.text
            if kind == "number":
                try:
                    new_val = float(new_val) if "." in new_val else int(new_val)
                except ValueError:
                    self._status = "valor inválido"
                    self._app.invalidate()
                    return
            self._cfg[key] = new_val
            self._dirty    = True
            self._status   = f"✓ {key}"
            self._mode     = "nav"
            event.app.layout.focus(event.app.layout.container)
            self._app.invalidate()

        @kb.add("escape", filter=is_edit)
        def _cancel_edit(event):
            self._mode   = "nav"
            self._status = ""
            event.app.layout.focus(event.app.layout.container)
            self._app.invalidate()

        # ── CHOICE bindings ───────────────────────────────────────────────────
        @kb.add("up",    filter=is_choice)
        @kb.add("left",  filter=is_choice)
        @kb.add("k",     filter=is_choice)
        def _choice_prev(_):
            _, _, _, _, opts = FLAT[self._cursor]
            self._choice_i = (self._choice_i - 1) % len(opts)
            self._app.invalidate()

        @kb.add("down",  filter=is_choice)
        @kb.add("right", filter=is_choice)
        @kb.add("j",     filter=is_choice)
        def _choice_next(_):
            _, _, _, _, opts = FLAT[self._cursor]
            self._choice_i = (self._choice_i + 1) % len(opts)
            self._app.invalidate()

        @kb.add("enter", filter=is_choice)
        def _confirm_choice(event):
            _, key, _, _, opts = FLAT[self._cursor]
            self._cfg[key] = opts[self._choice_i]
            self._dirty    = True
            self._status   = f"✓ {key}"
            self._mode     = "nav"
            self._app.invalidate()

        @kb.add("escape", filter=is_choice)
        def _cancel_choice(event):
            self._mode   = "nav"
            self._status = ""
            self._app.invalidate()

        self._app = Application(
            layout=layout,
            key_bindings=kb,
            style=PANEL_STYLE,
            full_screen=True,
            mouse_support=False,
        )
        self._app.run()
        return self._saved

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render_list(self) -> FormattedText:
        W, H = shutil.get_terminal_size((80, 24))
        ft: list[tuple[str, str]] = []

        def ln(parts):
            for p in parts: ft.append(p)
            ft.append(("", "\n"))

        def bdr(ch): return ("class:border", ch)
        def hbar(l, m, r): return [bdr(l), bdr(m * (W - 2)), bdr(r)]

        # Header
        title = "  RegBroker — Configurações"
        pad   = " " * max(0, W - len(title) - 2)
        dirty_mark = (" [não salvo]" if self._dirty else "")
        ln([bdr("┌"), bdr("─" * (W - 2)), bdr("┐")])
        ln([bdr("│"), ("class:title", title),
            ("class:dirty", dirty_mark),
            ("", " " * max(0, W - len(title) - len(dirty_mark) - 2)),
            bdr("│")])
        ln(hbar("├", "─", "┤"))

        # Items
        vis  = self._visible_rows()
        flat = FLAT
        # Group by section for rendering
        prev_sec = None
        rendered = 0

        for idx, (sec, key, label, kind, opts) in enumerate(flat):
            if idx < self._scroll:
                continue
            if rendered >= vis:
                break

            # Section header
            if sec != prev_sec:
                sec_line = f"  {sec}"
                sec_pad  = " " * max(0, W - len(sec_line) - 2)
                ln([bdr("│"), ("class:section", sec_line), ("", sec_pad), bdr("│")])
                prev_sec = sec
                rendered += 1
                if rendered >= vis:
                    break

            isel   = idx == self._cursor
            val    = self._cfg.get(key, "")
            disp   = _display_val(val, kind, opts)

            label_w = 30
            lbl     = label[:label_w].ljust(label_w)
            val_w   = W - label_w - 8  # 4 indent + 2 borders + 2 padding
            disp_t  = disp[:val_w] if len(disp) > val_w else disp

            # Add preview for ts_format
            if key == "ts_format" and val:
                preview = f"  ({_preview(str(val))})"
                disp_t  = disp_t + preview[:max(0, val_w - len(disp_t))]

            if isel:
                ln([
                    bdr("│"), ("class:cursor_key", f"  ▶ {lbl}  "),
                    ("class:cursor_val", disp_t),
                    ("", " " * max(0, W - len(lbl) - len(disp_t) - 8)),
                    bdr("│"),
                ])
            else:
                vcls = "class:secret_val" if kind == "secret" else "class:val"
                ln([
                    bdr("│"), ("class:key", f"    {lbl}  "),
                    (vcls, disp_t),
                    ("", " " * max(0, W - len(lbl) - len(disp_t) - 8)),
                    bdr("│"),
                ])
            rendered += 1

        # Fill remaining
        for _ in range(vis - rendered):
            ln([bdr("│"), ("", " " * (W - 2)), bdr("│")])

        ln(hbar("├", "─", "┤"))
        return FormattedText(ft)

    def _render_edit_header(self) -> FormattedText:
        W, _ = shutil.get_terminal_size((80, 24))
        _, key, label, kind, _ = FLAT[self._cursor]
        cur = str(self._cfg.get(key, ""))
        hint = "Enter confirmar   Esc cancelar"
        text = f"  Editando: {label}"
        pad  = " " * max(0, W - len(text) - len(hint))
        return FormattedText([
            ("class:edit_label", text),
            ("", pad),
            ("class:hint", hint + "  "),
        ])

    def _render_choice_bar(self) -> FormattedText:
        W, _ = shutil.get_terminal_size((80, 24))
        _, key, label, _, opts = FLAT[self._cursor]
        parts: list[tuple[str, str]] = [("class:edit_label", f"  {label}:  ")]
        for i, opt in enumerate(opts):
            if i:
                parts.append(("class:hint", "  "))
            if i == self._choice_i:
                parts.append(("class:choice_active", f"● {opt}"))
            else:
                parts.append(("class:choice_inactive", f"○ {opt}"))
        parts.append(("class:hint", "   ↑↓/←→ navegar  Enter confirmar  Esc cancelar"))
        return FormattedText(parts)

    def _render_footer(self) -> FormattedText:
        W, _ = shutil.get_terminal_size((80, 24))
        status = self._status
        keys = [("↑↓", "navegar"), ("Enter", "editar"),
                ("s", "salvar"), ("q", "fechar")]
        parts: list[tuple[str, str]] = [
            ("class:border", "│"),
            ("class:border", "  "),
        ]
        for i, (k, v) in enumerate(keys):
            if i: parts.append(("class:hint", "   "))
            parts.append(("class:hint_key", k))
            parts.append(("class:hint",     f" {v}"))

        if status:
            sty = "class:saved" if status.startswith("✓") or status == "saved" \
                  else "class:dirty"
            parts.append(("class:hint", "     "))
            parts.append((sty, status))

        ln = sum(len(t[1]) for t in parts)
        parts.append(("", " " * max(0, W - ln - 1)))
        parts.append(("class:border", "│"))
        return FormattedText(parts)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _visible_rows(self) -> int:
        _, rows = shutil.get_terminal_size((80, 24))
        # top border + title + sep + footer + possible edit area
        edit_h = 2 if self._mode == "edit" else (1 if self._mode == "choice" else 0)
        return max(3, rows - 5 - edit_h)

    def _clamp_scroll(self) -> None:
        vis = self._visible_rows()
        # Count rendered rows up to cursor (accounting for section headers)
        total_rendered = 0
        cursor_row     = 0
        prev_sec = None
        for idx, (sec, *_) in enumerate(FLAT):
            if sec != prev_sec:
                total_rendered += 1
                prev_sec = sec
            if idx == self._cursor:
                cursor_row = total_rendered
            total_rendered += 1

        if cursor_row < self._scroll:
            self._scroll = max(0, cursor_row - 1)
        elif cursor_row >= self._scroll + vis:
            self._scroll = cursor_row - vis + 1


# ── Display value helper ──────────────────────────────────────────────────────

def _display_val(val: Any, kind: str, opts: Any) -> str:
    s = str(val) if val != "" else "—"
    if kind == "secret" and s not in ("", "—"):
        return "●" * min(8, len(s)) + ("…" if len(s) > 8 else "")
    return s
