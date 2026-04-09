"""Interactive full-screen registry tree navigator.

  ↑↓  move cursor
  Enter  descend into subkey
  Esc  go back up
  y    confirm current selection
  q    cancel
"""
from __future__ import annotations

import shutil
from typing import Optional

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

try:
    from .. import bridge
except ImportError:
    import bridge

# ── Style ─────────────────────────────────────────────────────────────────────

NAV_STYLE = Style.from_dict({
    "border":       "#3d4752",
    "title":        "#00afff bold",
    "path":         "#79c0ff",
    "cursor_arrow": "#00ff87 bold",
    "cursor_name":  "#ffffff bold",
    "item":         "#c9d1d9",
    "count":        "#555577",
    "has_children": "#8b949e",
    "hint":         "#444455",
    "hint_key":     "#79c0ff",
    "scrollbar":    "#333344",
    "status":       "#555577",
})


class TreeNavigator:
    """Full-screen tree navigator. Call .run() to get selected path."""

    def __init__(self, hive_path: str, start_path: str = "\\"):
        self._hive      = hive_path
        self._stack: list[tuple[str, int, int]] = []  # (path, cursor, scroll)
        self._path      = start_path
        self._items: list[dict] = []
        self._cursor    = 0
        self._scroll    = 0
        self._result:   Optional[str] = None
        self._cancelled = False
        self._app:      Optional[Application] = None
        self._error     = ""

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self) -> Optional[str]:
        """Block until user confirms or cancels. Returns path or None."""
        self._load(self._path)
        self._app = Application(
            layout=Layout(Window(
                content=FormattedTextControl(self._render, focusable=True),
                dont_extend_height=False,
            )),
            key_bindings=self._bindings(),
            style=NAV_STYLE,
            full_screen=True,
            mouse_support=False,
            refresh_interval=None,
        )
        self._app.run()
        return None if self._cancelled else self._result

    # ── Key bindings ──────────────────────────────────────────────────────────

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("up")
        @kb.add("k")
        def _up(event):
            if self._cursor > 0:
                self._cursor -= 1
                self._clamp_scroll()
                self._app.invalidate()

        @kb.add("down")
        @kb.add("j")
        def _down(event):
            if self._cursor < len(self._items) - 1:
                self._cursor += 1
                self._clamp_scroll()
                self._app.invalidate()

        @kb.add("pageup")
        def _pgup(event):
            self._cursor = max(0, self._cursor - self._visible_rows())
            self._clamp_scroll()
            self._app.invalidate()

        @kb.add("pagedown")
        def _pgdn(event):
            self._cursor = min(len(self._items) - 1,
                               self._cursor + self._visible_rows())
            self._clamp_scroll()
            self._app.invalidate()

        @kb.add("enter")
        def _enter(event):
            if not self._items:
                return
            item = self._items[self._cursor]
            if item.get("num_subkeys", 0) == 0:
                # leaf — confirm immediately
                self._confirm()
                event.app.exit()
                return
            new_path = self._child_path(item["name"])
            self._stack.append((self._path, self._cursor, self._scroll))
            self._load(new_path)
            self._app.invalidate()

        @kb.add("escape")
        def _esc(event):
            if self._stack:
                path, cur, scroll = self._stack.pop()
                self._load(path)
                self._cursor = cur
                self._scroll = scroll
                self._app.invalidate()
            else:
                self._cancelled = True
                event.app.exit()

        @kb.add("y")
        @kb.add("Y")
        def _confirm_key(event):
            self._confirm()
            event.app.exit()

        @kb.add("q")
        @kb.add("Q")
        @kb.add("c-c")
        def _cancel(event):
            self._cancelled = True
            event.app.exit()

        return kb

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self) -> FormattedText:
        cols, rows = shutil.get_terminal_size((80, 24))
        W = cols

        ft: list[tuple[str, str]] = []

        def line(parts: list[tuple[str, str]]) -> None:
            for s in parts:
                ft.append(s)
            ft.append(("", "\n"))

        def border(ch: str) -> tuple[str, str]:
            return ("class:border", ch)

        def hbar(left: str, mid: str, right: str) -> list[tuple[str, str]]:
            return [border(left), border(mid * (W - 2)), border(right)]

        # ── Top border ────────────────────────────────────────────────────────
        line(hbar("┌", "─", "┐"))

        # ── Title / path ──────────────────────────────────────────────────────
        depth_indicator = "  " * len(self._stack)
        title_text = f"  {depth_indicator}{self._path}"
        if len(title_text) > W - 4:
            title_text = "  …" + title_text[-(W - 7):]
        pad = " " * max(0, W - len(title_text) - 2)
        line([border("│"), ("class:title", title_text), ("", pad), border("│")])

        # ── Separator ─────────────────────────────────────────────────────────
        line(hbar("├", "─", "┤"))

        # ── Items area ────────────────────────────────────────────────────────
        vis = self._visible_rows()

        if self._error:
            msg = f"  Error: {self._error}"[:W - 4]
            line([border("│"), ("class:item", msg),
                  ("", " " * max(0, W - len(msg) - 2)), border("│")])
            for _ in range(vis - 1):
                line([border("│"), ("", " " * (W - 2)), border("│")])
        elif not self._items:
            msg = "  (no subkeys)"
            line([border("│"), ("class:count", msg),
                  ("", " " * (W - len(msg) - 2)), border("│")])
            for _ in range(vis - 1):
                line([border("│"), ("", " " * (W - 2)), border("│")])
        else:
            visible = self._items[self._scroll: self._scroll + vis]
            for row_i, item in enumerate(visible):
                idx    = row_i + self._scroll
                isel   = idx == self._cursor
                name   = item.get("name", "?")
                nsubs  = item.get("num_subkeys", 0)
                nvals  = item.get("num_values",  0)

                count  = f" [{nsubs:3d}▸]" if nsubs else f" [{nvals:3d} ]"
                inner  = W - 2                     # │...│
                cnt_w  = len(count)
                arr_w  = 4                          # " ▶  " or "    "
                name_w = inner - arr_w - cnt_w - 1 # 1 space padding right
                if len(name) > name_w:
                    name = name[:name_w - 1] + "…"
                pad_name = " " * (name_w - len(name))

                if isel:
                    row = [
                        border("│"),
                        ("class:cursor_arrow", " ▶  "),
                        ("class:cursor_name",  name),
                        ("class:cursor_name",  pad_name + " "),
                        ("class:has_children", count),
                        border("│"),
                    ]
                else:
                    arrow = " ▸  " if nsubs else "    "
                    row = [
                        border("│"),
                        ("class:has_children" if nsubs else "class:count", arrow),
                        ("class:item",         name),
                        ("class:count",        pad_name + " "),
                        ("class:count",        count),
                        border("│"),
                    ]
                line(row)

            # Fill empty rows
            for _ in range(vis - len(visible)):
                line([border("│"), ("", " " * (W - 2)), border("│")])

        # ── Status bar ────────────────────────────────────────────────────────
        if self._items:
            n   = len(self._items)
            pct = int(self._cursor / max(1, n - 1) * 100) if n > 1 else 100
            status = f" {self._cursor + 1}/{n} — {pct}% "
        else:
            status = " 0 items "
        s_pad = " " * max(0, W - len(status) - 2)
        line([border("├"), ("class:status", status), ("class:scrollbar", s_pad), border("┤")])

        # ── Footer ────────────────────────────────────────────────────────────
        keys = [
            ("↑↓", "move"),
            ("Enter", "descend"),
            ("Esc", "back"),
            ("y", "select"),
            ("q", "cancel"),
        ]
        parts: list[tuple[str, str]] = [border("│"), ("", "  ")]
        for i, (k, v) in enumerate(keys):
            if i:
                parts.append(("class:hint", "   "))
            parts.append(("class:hint_key", k))
            parts.append(("class:hint",     f" {v}"))
        hint_len = sum(len(t[1]) for t in parts)
        parts.append(("", " " * max(0, W - hint_len - 1)))
        parts.append(border("│"))
        line(parts)

        line(hbar("└", "─", "┘"))

        return FormattedText(ft)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _visible_rows(self) -> int:
        _, rows = shutil.get_terminal_size((80, 24))
        # top border + title + sep + status + footer + bottom border = 6 lines
        return max(3, rows - 8)

    def _clamp_scroll(self) -> None:
        vis = self._visible_rows()
        if self._cursor < self._scroll:
            self._scroll = self._cursor
        elif self._cursor >= self._scroll + vis:
            self._scroll = self._cursor - vis + 1

    def _load(self, path: str) -> None:
        self._error = ""
        try:
            data = bridge.ls(self._hive, path)
            self._items   = data.get("subkeys", [])
            self._path    = path
            self._cursor  = 0
            self._scroll  = 0
        except bridge.BridgeError as e:
            self._error = str(e)
            self._items = []
            self._path  = path

    def _child_path(self, name: str) -> str:
        base = self._path.rstrip("\\")
        return base + "\\" + name

    def _confirm(self) -> None:
        if self._items and self._cursor < len(self._items):
            self._result = self._child_path(self._items[self._cursor]["name"])
        else:
            self._result = self._path
