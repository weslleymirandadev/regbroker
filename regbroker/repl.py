"""RegBroker interactive REPL — Claude Code-style interface."""
from __future__ import annotations

import os
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box

from . import bridge, config as cfg_mod
from .ai.openrouter import OpenRouterClient, OpenRouterError
from .tui.tree_nav import TreeNavigator
from .tui.editor import Editor
from .tui.config_panel import ConfigPanel

console = Console()

# ── Prompt style ─────────────────────────────────────────────────────────────

PROMPT_STYLE = Style.from_dict({
    "rb":    "#00afff bold",       # "regbroker"
    "ctx":   "#3d4752",            # brackets
    "hive":  "#5fd7ff",            # hive name
    "sep":   "#3d4752",            # › separator
    "path":  "#79c0ff",            # registry path
    "arrow": "#00ff87 bold",       # ❯
    "completion-menu.completion":         "bg:#1a1a2e #c9d1d9",
    "completion-menu.completion.current": "bg:#0d3b66 #ffffff bold",
    "auto-suggestion":                    "#333344",
})

# ── Completer ─────────────────────────────────────────────────────────────────

class _Completer(Completer):
    COMMANDS = [
        "open", "ls", "cd", "info", "hex", "find", "search",
        "note", "report", "recover", "models", "model",
        "config", "help", "exit",
    ]

    def __init__(self, state: "State"):
        self._s = state

    def get_completions(self, document, complete_event):
        text  = document.text_before_cursor
        words = text.split()
        space = text.endswith(" ")

        # Complete command name
        if not words or (len(words) == 1 and not space):
            partial = words[0] if words else ""
            for c in self.COMMANDS:
                if c.startswith(partial):
                    yield Completion(c, -len(partial))
            return

        cmd = words[0].lower()

        # Complete registry path after cd / ls / find / search
        if cmd in ("cd", "ls", "find", "search") and self._s.hive_open:
            partial = words[-1] if len(words) > 1 else ""
            yield from self._complete_path(partial)

        # Complete value name after info hex note
        elif cmd in ("hex",) and self._s.hive_open:
            partial = words[-1] if len(words) > 1 else ""
            try:
                data = bridge.ls(self._s.hive_path, self._s.path)
                for v in data.get("values", []):
                    name = v.get("name", "") or "(Default)"
                    if name.lower().startswith(partial.lower()):
                        yield Completion(name, -len(partial))
            except Exception:
                pass

        elif cmd == "report" and len(words) == 2 and not space:
            for sub in ("edit", "save"):
                if sub.startswith(words[1]):
                    yield Completion(sub, -len(words[1]))

        elif cmd == "model" and self._s.model_cache:
            partial = words[-1] if len(words) > 1 else ""
            for m in self._s.model_cache:
                mid = m.get("id", "")
                if mid.lower().startswith(partial.lower()):
                    yield Completion(mid, -len(partial))

    def _complete_path(self, partial: str):
        try:
            if "\\" in partial:
                sep  = partial.rfind("\\")
                base = partial[:sep] or "\\"
                pfx  = partial[sep + 1:]
            else:
                base = self._s.path
                pfx  = partial
            data = bridge.ls(self._s.hive_path, base)
            for sk in data.get("subkeys", []):
                name = sk.get("name", "")
                if name.lower().startswith(pfx.lower()):
                    full = base.rstrip("\\") + "\\" + name
                    yield Completion(full, -len(partial),
                                     display=f"[{name}]",
                                     display_meta=f"{sk.get('num_subkeys',0)}▸")
        except Exception:
            pass


# ── State ─────────────────────────────────────────────────────────────────────

class State:
    def __init__(self):
        self.hive_path: str  = ""
        self.hive_name: str  = ""
        self.hive_info: dict = {}
        self.path:      str  = "\\"
        self.hive_open: bool = False
        self.config:    dict = {}
        self.ai:        Optional[OpenRouterClient] = None
        self.model_cache: list = []
        self.report_md: str  = ""      # last generated report
        self.report_path: Path = Path("laudo_pericial.md")

    def make_prompt(self) -> HTML:
        if self.hive_open:
            # Shorten path for display
            parts  = [p for p in self.path.split("\\") if p]
            disp   = " › ".join(parts[-2:]) if len(parts) > 2 else self.path
            return HTML(
                f'<rb>regbroker</rb>'
                f'<ctx> [</ctx>'
                f'<hive>{_he(self.hive_name)}</hive>'
                f'<sep> › </sep>'
                f'<path>{_he(disp)}</path>'
                f'<ctx>]</ctx>'
                f'<arrow> ❯ </arrow>'
            )
        return HTML('<rb>regbroker</rb><arrow> ❯ </arrow>')


def _he(s: str) -> str:
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


# ── REPL ──────────────────────────────────────────────────────────────────────

class Repl:
    def __init__(self, config: dict):
        global _cfg
        self.st = State()
        self.st.config = config
        _cfg = config   # make timestamp formatter config-aware

        self._apply_ai_config()

        hist_dir = Path.home() / ".regbroker"
        hist_dir.mkdir(exist_ok=True)

        self._session: PromptSession = PromptSession(
            history=FileHistory(str(hist_dir / "history")),
            auto_suggest=AutoSuggestFromHistory(),
            completer=_Completer(self.st),
            style=PROMPT_STYLE,
            complete_while_typing=True,
        )
        self._editor        = Editor()
        self._config_panel  = ConfigPanel()

    def _apply_ai_config(self) -> None:
        """Instantiate / update the AI client from current config."""
        api_key = self.st.config.get("api_key", "")
        model   = self.st.config.get("model", "anthropic/claude-3.5-haiku")
        if api_key:
            if self.st.ai:
                self.st.ai.set_model(model)
                self.st.ai.api_key = api_key
            else:
                self.st.ai = OpenRouterClient(api_key, model)

    def run(self, initial_hive: str = "") -> None:
        _print_banner()
        if initial_hive:
            self._open([initial_hive])
        while True:
            try:
                line = self._session.prompt(self.st.make_prompt()).strip()
            except KeyboardInterrupt:
                console.print()
                continue
            except EOFError:
                console.print("[dim]bye.[/dim]")
                break
            if not line:
                continue
            self._dispatch(line)

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _dispatch(self, line: str) -> None:
        tokens = _tok(line)
        if not tokens:
            return
        cmd, *args = tokens
        try:
            {
                "open":    self._open,
                "ls":      self._ls,
                "dir":     self._ls,
                "cd":      self._cd,
                "info":    self._info,
                "hex":     self._hex,
                "find":    self._find,
                "search":  self._search,
                "note":    self._note,
                "report":  self._report,
                "recover": self._recover,
                "models":  self._models,
                "model":   self._model,
                "config":  self._config,
                "help":    self._help,
                "exit":    lambda _: sys.exit(0),
                "quit":    lambda _: sys.exit(0),
            }.get(cmd.lower(), self._unknown)(args)
        except bridge.BridgeError as e:
            console.print(f"[bold red]core error:[/bold red] {e}")
        except OpenRouterError as e:
            console.print(f"[bold red]AI error:[/bold red] {e}")
        except KeyboardInterrupt:
            console.print("\n[dim]interrupted.[/dim]")
        except Exception as e:
            console.print(f"[bold red]error:[/bold red] {e}")

    def _unknown(self, _):
        console.print("[dim]Unknown command. Type [bold]help[/bold] for a list.[/dim]")

    # ── Commands ──────────────────────────────────────────────────────────────

    def _open(self, args: list[str]) -> None:
        if not args:
            console.print("[dim]Usage:[/dim] open [bold]<hivefile>[/bold]")
            return
        path = " ".join(args)
        if not os.path.exists(path):
            console.print(f"[red]File not found:[/red] {path}")
            return
        console.print(f"[dim]Loading[/dim] {path} …")
        info = bridge.hive_info(path)
        self.st.hive_path = path
        self.st.hive_name = Path(path).name
        self.st.hive_info = info
        self.st.path      = "\\"
        self.st.hive_open = True
        _print_hive_info(info, path)

    def _ls(self, args: list[str]) -> None:
        if not self._need_hive(): return
        path = self._resolve(args[0]) if args else self.st.path
        data = bridge.ls(self.st.hive_path, path)
        _print_ls(data)

    def _cd(self, args: list[str]) -> None:
        if not self._need_hive(): return

        if args:
            # Direct navigation: cd <path>
            target = args[0]
            if target in ("..", "..\\"):
                parts = self.st.path.rstrip("\\").rsplit("\\", 1)
                self.st.path = parts[0] if parts[0] else "\\"
                return
            if target in ("\\", "/"):
                self.st.path = "\\"
                return
            new_path = self._resolve(target)
            # Validate
            bridge.ls(self.st.hive_path, new_path)  # raises on error
            self.st.path = new_path
        else:
            # Interactive tree navigator
            nav = TreeNavigator(self.st.hive_path, self.st.path)
            result = nav.run()
            if result:
                self.st.path = result
                console.print(f"[dim]→[/dim] [bold cyan]{self.st.path}[/bold cyan]")

    def _info(self, args: list[str]) -> None:
        if not self._need_hive(): return
        path = self._resolve(args[0]) if args else self.st.path
        data = bridge.ls(self.st.hive_path, path)
        _print_info(data)

    def _hex(self, args: list[str]) -> None:
        if not self._need_hive(): return
        if not args:
            console.print("[dim]Usage:[/dim] hex [bold]<value_name>[/bold]")
            return
        name = " ".join(args)
        data = bridge.cat(self.st.hive_path, self.st.path, name)
        v    = data.get("value", {})
        dump = data.get("hex_dump", "")
        console.print(f"\n[bold cyan]{v.get('name','?')}[/bold cyan]  "
                      f"[dim]{v.get('type','?')}  {v.get('size',0):,} bytes[/dim]")
        console.print(Panel(f"[dim]{dump}[/dim]", border_style="dim"))

    def _find(self, args: list[str]) -> None:
        if not self._need_hive(): return
        if not args:
            console.print("[dim]Usage:[/dim] find [bold]<pattern>[/bold]")
            return
        pattern = " ".join(args)
        console.print(f"[dim]searching keys…[/dim]")
        results = bridge.find(self.st.hive_path, self.st.path, pattern)
        if not results:
            console.print("[dim]no results.[/dim]")
            return
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold dim", padding=(0,1))
        t.add_column("Path", style="blue")
        t.add_column("Last write", style="dim")
        t.add_column("Subs", justify="right", style="dim")
        for k in results:
            t.add_row(k.get("path","?"), _ts(k.get("timestamp")),
                      str(k.get("num_subkeys",0)))
        console.print(t)
        console.print(f"[dim]{len(results)} result(s)[/dim]")

    def _search(self, args: list[str]) -> None:
        if not self._need_hive(): return
        if not args:
            console.print("[dim]Usage:[/dim] search [bold]<text>[/bold]")
            return
        pattern = " ".join(args)
        console.print(f"[dim]searching values…[/dim]")
        results = bridge.search(self.st.hive_path, self.st.path, pattern)
        if not results:
            console.print("[dim]no results.[/dim]")
            return
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold dim", padding=(0,1))
        t.add_column("Key", style="blue")
        t.add_column("Value")
        t.add_column("Type", style="dim")
        t.add_column("Data", overflow="fold")
        for item in results:
            k = item.get("key",{})
            v = item.get("value",{})
            t.add_row(k.get("path","?"), v.get("name","") or "(Default)",
                      v.get("type",""), str(v.get("value",""))[:100])
        console.print(t)

    def _note(self, args: list[str]) -> None:
        if not self._need_hive(): return
        data  = bridge.ls(self.st.hive_path, self.st.path)
        key   = data.get("key", {})
        vals  = data.get("values", [])
        subs  = data.get("subkeys", [])
        now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ── Metadata block ────────────────────────────────────────────────────
        ts_iso  = _ts_plain(key.get("timestamp"))
        ts_unix = key.get("timestamp_unix", "")
        lines = [
            f"\n---\n",
            f"## `{self.st.path}`",
            f"",
            f"| Campo | Valor |",
            f"|-------|-------|",
            f"| Hive | `{self.st.hive_name}` |",
            f"| Capturado (local) | `{now}` |",
            f"| **Última escrita (UTC)** | **`{ts_iso}`** |",
        ]
        if ts_unix:
            lines.append(f"| Última escrita (Unix) | `{ts_unix}` |")
        lines += [
            f"| Cell offset | `0x{key.get('cell_offset', 0):08x}` |",
            f"| Subchaves | {key.get('num_subkeys', 0)} |",
            f"| Valores | {key.get('num_values', 0)} |",
            f"",
        ]

        # ── Values table ──────────────────────────────────────────────────────
        if vals:
            lines += [
                "### Valores",
                "",
                "| Valor | Tipo | Tamanho | Dado |",
                "|-------|------|---------|------|",
            ]
            for v in vals:
                name = (v.get("name", "") or "(Default)").replace("|", "\\|")
                val  = str(v.get("value", "")).replace("|", "\\|")
                if len(val) > 120:
                    val = val[:117] + "…"
                lines.append(
                    f"| `{name}` | {v.get('type','')} "
                    f"| {v.get('size', 0):,} B | {val} |"
                )
            lines.append("")

        # ── Subkeys with timestamps ───────────────────────────────────────────
        if subs:
            lines += [
                "### Subchaves",
                "",
                "| Subchave | Última escrita (UTC) | Unix | Subs | Vals |",
                "|----------|----------------------|------|------|------|",
            ]
            for sk in subs:
                sk_ts    = _ts_plain(sk.get("timestamp"))
                sk_unix  = sk.get("timestamp_unix", "")
                lines.append(
                    f"| `{sk.get('name','')}` "
                    f"| `{sk_ts}` "
                    f"| `{sk_unix}` "
                    f"| {sk.get('num_subkeys',0)} "
                    f"| {sk.get('num_values',0)} |"
                )
            lines.append("")

        lines.append("")

        entry = "\n".join(lines)

        # Append to data.md
        md_path = Path(self.st.config.get("note_file", "data.md"))
        if not md_path.exists():
            md_path.write_text("# RegBroker Notes\n\n", encoding="utf-8")
        with open(md_path, "a", encoding="utf-8") as f:
            f.write(entry)

        console.print(f"[green]✓[/green] Appended to [bold]{md_path}[/bold]")
        console.print(f"[dim]Opening editor…[/dim]")

        self._editor.run(md_path)

    def _report(self, args: list[str]) -> None:
        if not self._need_hive(): return

        sub = args[0].lower() if args else ""

        if sub == "edit":
            if not self.st.report_path.exists():
                console.print("[dim]No report yet. Run [bold]report[/bold] first.[/dim]")
                return
            self._editor.run(self.st.report_path)
            return

        if sub == "save":
            if not self.st.report_path.exists():
                console.print("[dim]No report yet. Run [bold]report[/bold] first.[/dim]")
                return
            pdf_path = str(self.st.report_path.with_suffix(".pdf"))
            if len(args) > 1:
                pdf_path = args[1]
            console.print(f"[dim]Generating PDF…[/dim]")
            from .ai.report import save_pdf
            md_text = self.st.report_path.read_text(encoding="utf-8")
            out = save_pdf(md_text, pdf_path)
            console.print(f"[green]✓[/green] PDF saved: [bold]{out}[/bold]")
            return

        # Generate new report
        if not self._need_ai(): return

        console.print()
        console.rule("[bold cyan]Gerando laudo pericial[/bold cyan]", style="cyan")
        console.print(f"[dim]Modelo: {self.st.ai.model}[/dim]")
        console.print()

        # Collect context
        ls_data  = bridge.ls(self.st.hive_path, self.st.path)
        recovery = None
        try:
            console.print("[dim]Escaneando artefatos deletados…[/dim]")
            recovery = bridge.recover(self.st.hive_path)
        except Exception:
            pass

        from .ai.report import build_context, generate_report
        context  = build_context(self.st.hive_path, self.st.hive_info,
                                  ls_data, recovery)
        perito   = self.st.config.get("perito_name", "Perito Não Identificado")

        full = generate_report(
            self.st.ai, context, perito,
            on_chunk=lambda c: console.print(c, end="", highlight=False, markup=False),
        )
        console.print()
        console.rule(style="dim")

        # Save markdown
        report_name = f"laudo_{Path(self.st.hive_path).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        self.st.report_path = Path(report_name)
        self.st.report_path.write_text(full, encoding="utf-8")
        console.print(f"\n[green]✓[/green] Laudo salvo em [bold]{report_name}[/bold]")
        console.print("[dim]Use [bold]report edit[/bold] para editar ou [bold]report save[/bold] para exportar PDF.[/dim]")

    def _recover(self, _args: list[str]) -> None:
        if not self._need_hive(): return
        console.print("[dim]Scanning for deleted entries…[/dim]")
        data = bridge.recover(self.st.hive_path)
        _print_recovery(data)

    def _models(self, _args: list[str]) -> None:
        if not self._need_ai(): return
        console.print("[dim]Fetching models…[/dim]")
        models = self.st.ai.list_models()
        self.st.model_cache = models
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold dim", padding=(0,1))
        t.add_column("ID")
        t.add_column("Name", style="dim")
        t.add_column("Context", justify="right", style="dim")
        for m in sorted(models, key=lambda x: x.get("id","")):
            mid = m.get("id","")
            is_cur = mid == self.st.ai.model
            t.add_row(
                f"[bold cyan]{mid}[/bold cyan]" if is_cur else mid,
                m.get("name",""),
                f"{m.get('context_length',0):,}",
            )
        console.print(t)
        console.print(f"[dim]current: [cyan]{self.st.ai.model}[/cyan][/dim]")

    def _model(self, args: list[str]) -> None:
        if not self._need_ai(): return
        if not args:
            console.print(f"[dim]current model:[/dim] [cyan]{self.st.ai.model}[/cyan]")
            return
        self.st.ai.set_model(args[0])
        self.st.config["model"] = args[0]
        cfg_mod.save(self.st.config)
        console.print(f"[green]✓[/green] model → [cyan]{args[0]}[/cyan]")

    def _config(self, args: list[str]) -> None:
        global _cfg
        # "config" or "config show" → open interactive panel
        if not args or args[0] in ("", "show", "edit"):
            saved = self._config_panel.run(self.st.config)
            if saved:
                # Config was saved — re-apply AI settings and timestamp fmt
                _cfg = self.st.config
                self._apply_ai_config()
                console.print("[green]✓[/green] Configurações salvas.")
            else:
                console.print("[dim]Configurações fechadas sem salvar.[/dim]")
            return

        # Quick "config set key value" for scripted use
        if args[0] == "set" and len(args) >= 3:
            key, val = args[1], " ".join(args[2:])
            self.st.config[key] = val
            _cfg = self.st.config
            self._apply_ai_config()
            cfg_mod.save(self.st.config)
            console.print(f"[green]✓[/green] {key} = {val}")
            return

        console.print("[dim]Use [bold]config[/bold] para abrir o painel  "
                      "ou [bold]config set[/bold] <chave> <valor>")

    def _help(self, args: list[str]) -> None:
        console.print()
        t = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
        t.add_column("cmd",  style="bold cyan", no_wrap=True)
        t.add_column("desc", style="dim")
        rows = [
            ("open <file>",        "Open a registry hive file"),
            ("ls [path]",          "List subkeys and values at path"),
            ("cd [path]",          "Navigate (no arg → interactive tree)"),
            ("info [path]",        "Show values as formatted table"),
            ("hex <value>",        "Hex dump of a value"),
            ("find <pattern>",     "Find subkeys by name"),
            ("search <text>",      "Search value names and data"),
            ("note",               "Append current key to data.md and open editor"),
            ("report",             "Generate AI forensic report (markdown)"),
            ("report edit",        "Open last report in editor"),
            ("report save [file]", "Export report as PDF"),
            ("recover",            "Scan for deleted registry entries"),
            ("models",             "List available AI models"),
            ("model <id>",         "Switch AI model"),
            ("config",             "Open interactive settings panel"),
            ("config set k v",     "Set a value directly (scriptável)"),
            ("exit",               "Quit"),
        ]
        for cmd, desc in rows:
            t.add_row(cmd, desc)
        console.print(t)
        console.print("[dim]Tab completion available · Ctrl+C cancel · Ctrl+D exit[/dim]\n")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _need_hive(self) -> bool:
        if not self.st.hive_open:
            console.print("[dim]No hive open. Use:[/dim] open [bold]<hivefile>[/bold]")
            return False
        return True

    def _need_ai(self) -> bool:
        if not self.st.ai:
            console.print(
                "[dim]IA não configurada.[/dim] "
                "Use [bold]config[/bold] para abrir o painel de configurações "
                "e preencha a API Key e o modelo."
            )
            return False
        return True

    def _resolve(self, p: str) -> str:
        if not p or p == "\\":
            return "\\"
        if p.startswith("\\"):
            return p
        base = self.st.path.rstrip("\\")
        return (base + "\\" + p).replace("\\\\", "\\")


# ── Rendering helpers ─────────────────────────────────────────────────────────

# Module-level config reference — updated by Repl on init and on config change
_cfg: dict = {}


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse ISO timestamp from core (always UTC) into a datetime."""
    try:
        return datetime.strptime(
            ts.rstrip("Z").replace("T", " "), "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _apply_format(dt: datetime, cfg: dict) -> str:
    """Convert a UTC datetime to the configured display format + timezone."""
    tz_name = cfg.get("timezone", "UTC")
    if tz_name and tz_name != "UTC":
        try:
            from zoneinfo import ZoneInfo
            dt = dt.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass

    fmt = cfg.get("ts_format", "ISO")
    if fmt == "BR":
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    if fmt == "US":
        return dt.strftime("%m/%d/%Y %H:%M:%S")
    if fmt == "UNIX":
        return str(int(dt.timestamp()))
    if fmt == "custom":
        try:
            return dt.strftime(cfg.get("ts_custom_fmt", "%d/%m/%Y %H:%M:%S"))
        except Exception:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    # ISO (default)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _ts(ts: str | None) -> str:
    """Rich-markup timestamp string using current config."""
    if not ts or ts.startswith("1601"):
        return "[dim]—[/dim]"
    dt = _parse_iso(ts)
    if dt is None:
        return ts
    return _apply_format(dt, _cfg)


def _ts_plain(ts: str | None) -> str:
    """Plain timestamp string (no Rich markup) using current config."""
    if not ts or ts.startswith("1601"):
        return "—"
    dt = _parse_iso(ts)
    if dt is None:
        return ts.replace("T", " ").rstrip("Z")
    return _apply_format(dt, _cfg)

_TYPE_COLOR = {
    "REG_SZ":        "green",
    "REG_EXPAND_SZ": "bright_green",
    "REG_DWORD":     "yellow",
    "REG_QWORD":     "bright_yellow",
    "REG_BINARY":    "magenta",
    "REG_MULTI_SZ":  "cyan",
}

def _tcolor(tname: str) -> str:
    return _TYPE_COLOR.get(tname, "white")


def _print_banner() -> None:
    console.print(Panel.fit(
        "[bold cyan]RegBroker[/bold cyan] [dim]v1.0.0[/dim]\n"
        "[dim]Windows Registry Forensics · AI Analysis[/dim]\n"
        "[dim]type [bold white]help[/bold white] for commands[/dim]",
        border_style="cyan", padding=(0, 3),
    ))
    console.print()


def _print_hive_info(info: dict, path: str) -> None:
    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    t.add_column("k", style="dim", no_wrap=True)
    t.add_column("v")
    t.add_row("file",         path)
    t.add_row("root",         info.get("root_name","?"))
    t.add_row("version",      info.get("version","?"))
    t.add_row("last write",   _ts(info.get("timestamp")))
    t.add_row("size",         f"{info.get('hive_size',0):,} bytes")
    t.add_row("root subkeys", str(info.get("root_subkeys",0)))
    console.print(Panel(t, border_style="blue", title="[bold]hive loaded[/bold]"))


def _print_ls(data: dict) -> None:
    key    = data.get("key",{})
    subkeys = data.get("subkeys",[])
    values  = data.get("values",[])

    console.print(
        f"\n  [bold blue]{key.get('path','?')}[/bold blue]  "
        f"[dim]{_ts(key.get('timestamp'))} UTC[/dim]"
    )
    console.rule(style="dim")

    if subkeys:
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold dim", padding=(0,1), show_header=False)
        t.add_column("name",       style="bold blue", no_wrap=True)
        t.add_column("last write", style="dim",       justify="right")
        t.add_column("▸",          justify="right",   style="dim")
        for sk in subkeys:
            t.add_row(
                f"[{sk['name']}]",
                _ts(sk.get("timestamp")),
                str(sk.get("num_subkeys", 0)),
            )
        console.print(t)

    if values:
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold dim", padding=(0,1), show_header=False)
        t.add_column("name", no_wrap=True)
        t.add_column("type", no_wrap=True)
        t.add_column("data", overflow="fold")
        for v in values:
            c    = _tcolor(v.get("type",""))
            name = v.get("name","") or "[italic](Default)[/italic]"
            t.add_row(name,
                      f"[{c}]{v.get('type','?')}[/{c}]",
                      str(v.get("value",""))[:120])
        console.print(t)

    if not subkeys and not values:
        console.print("  [dim](empty key)[/dim]")
    console.print()


def _print_info(data: dict) -> None:
    key   = data.get("key",{})
    vals  = data.get("values",[])
    subs  = data.get("subkeys",[])

    title = key.get("path","?")
    console.print(f"\n[bold blue]{title}[/bold blue]")
    console.print(
        f"  [dim]Last write:[/dim] [bold]{_ts(key.get('timestamp'))} UTC[/bold]  "
        f"[dim](unix: {key.get('timestamp_unix', '?')})[/dim]  "
        f"[dim]·  Subkeys: {key.get('num_subkeys',0)}  "
        f"Values: {key.get('num_values',0)}  "
        f"Offset: 0x{key.get('cell_offset',0):08x}[/dim]"
    )
    console.rule(style="dim")

    if vals:
        t = Table(box=box.ROUNDED, header_style="bold", padding=(0, 1))
        t.add_column("Value name", style="cyan", no_wrap=True)
        t.add_column("Type",       no_wrap=True)
        t.add_column("Size",       justify="right", style="dim")
        t.add_column("Data",       overflow="fold")
        for v in vals:
            c    = _tcolor(v.get("type",""))
            name = v.get("name","") or "[italic dim](Default)[/italic dim]"
            val  = str(v.get("value",""))
            if len(val) > 200:
                val = val[:197] + "…"
            t.add_row(
                name,
                f"[{c}]{v.get('type','?')}[/{c}]",
                f"{v.get('size',0):,}",
                val,
            )
        console.print(t)
    else:
        console.print("  [dim](no values)[/dim]")

    if subs:
        console.print(f"\n  [dim]Subkeys ({len(subs)}):[/dim]")
        cols = shutil.get_terminal_size((80,24)).columns
        per_row = max(1, cols // 30)
        for i in range(0, len(subs), per_row):
            row = subs[i:i+per_row]
            console.print("  " + "  ".join(
                f"[blue][{s.get('name','')}][/blue]" for s in row
            ))
    console.print()


def _print_recovery(data: dict) -> None:
    dk = data.get("deleted_keys",[])
    dv = data.get("deleted_values",[])
    console.print(Panel(
        f"HBINs scanned: [bold]{data.get('hbins_scanned',0)}[/bold]   "
        f"Free cells: [bold]{data.get('free_cells_scanned',0)}[/bold]   "
        f"Deleted keys: [bold yellow]{len(dk)}[/bold yellow]   "
        f"Deleted values: [bold magenta]{len(dv)}[/bold magenta]",
        title="Recovery", border_style="yellow",
    ))
    if dk:
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold yellow", padding=(0,1))
        t.add_column("Key name",    style="yellow")
        t.add_column("Last write",  style="dim")
        t.add_column("Parent reachable")
        for rk in dk:
            k2 = rk.get("key",{})
            t.add_row(
                k2.get("name","?"),
                _ts(k2.get("timestamp")),
                "[green]yes[/green]" if rk.get("parent_reachable") else "[red]no[/red]",
            )
        console.print(t)
    if dv:
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold magenta", padding=(0,1))
        t.add_column("Value",  style="magenta")
        t.add_column("Type",   style="dim")
        t.add_column("Data",   overflow="fold")
        t.add_column("Intact")
        for rv in dv:
            v2 = rv.get("value",{})
            t.add_row(
                v2.get("name","?") or "(Default)",
                v2.get("type","?"),
                str(v2.get("value",""))[:80],
                "[green]yes[/green]" if rv.get("data_intact") else "[dim]no[/dim]",
            )
        console.print(t)


# ── Tokenizer ─────────────────────────────────────────────────────────────────

def _tok(line: str) -> list[str]:
    tokens, cur, q = [], "", False
    for ch in line:
        if ch == '"':
            q = not q
        elif ch == " " and not q:
            if cur:
                tokens.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        tokens.append(cur)
    return tokens
