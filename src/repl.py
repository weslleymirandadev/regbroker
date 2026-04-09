"""RegBroker interactive REPL — Claude Code-style interface with Textual."""
from __future__ import annotations

import asyncio
import os
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Static
from textual.binding import Binding
from rich.text import Text
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box
import random

try:
    from . import bridge, config as cfg_mod
    from .ai.openrouter import OpenRouterClient, OpenRouterError
    from .tui.tree_nav import TreeNavigator
    from .tui.editor import Editor
    from .tui.config_panel import ConfigPanel
except ImportError:
    # Fallback for direct execution
    import bridge
    import config as cfg_mod
    from ai.openrouter import OpenRouterClient, OpenRouterError
    from tui.tree_nav import TreeNavigator
    from tui.editor import Editor
    from tui.config_panel import ConfigPanel

console = Console()

# ── Textual Widgets ───────────────────────────────────────────────────────────

class ClaudeCodeInput(Input):
    """Custom input widget with Claude Code styling."""
    
    def __init__(self, **kwargs):
        super().__init__(placeholder="", **kwargs)
        self.cursor_blink = True
        
    def render_prefix(self) -> Text:
        """Render the > prefix."""
        prefix = Text("> ")
        prefix.stylize("bold blue")
        return prefix


class ClaudeCodeLine(Static):
    """Horizontal line widget."""
    
    def __init__(self, **kwargs):
        super().__init__(" " * 80, **kwargs)
        
    def render(self) -> Text:
        """Render horizontal line."""
        terminal_width = self.size.width if self.size else 80
        line = " " * terminal_width
        text = Text(line)
        text.stylize("dim")
        return text


class OutputArea(Static):
    """Output display area."""
    
    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self.can_focus = False
        self._output_lines = []
        
    def add_output(self, text: str, style: str = "") -> None:
        """Add text to output area."""
        self._output_lines.append((text, style))
        self._update_display()
        
    def _update_display(self) -> None:
        """Update the display with all output lines."""
        if not self._output_lines:
            self.update("")
            return
            
        # Show last 20 lines to avoid overflow
        recent_lines = self._output_lines[-20:]
        formatted_lines = []
        
        for text, style in recent_lines:
            if style:
                formatted = Text(text)
                formatted.stylize(style)
                formatted_lines.append(str(formatted))
            else:
                formatted_lines.append(text)
                
        self.update("\n".join(formatted_lines))


# ── State Management ───────────────────────────────────────────────────────────

class State:
    """Global REPL state."""
    def __init__(self):
        self.hive_name: str = ""
        self.hive_path: str = ""
        self.hive_info: dict = {}
        self.path:      str  = "\\"
        self.hive_open: bool = False
        self.config:    dict = {}
        self.ai:        Optional[OpenRouterClient] = None
        self.model_cache: list = []
        self.report_md: str  = ""      # last generated report
        self.report_path: Path = Path("laudo_pericial.md")

# ── Auto Suggest ─────────────────────────────────────────────────────────────

class _RegBrokerAutoSuggest:
    """Claude Code-style intelligent auto-suggestions."""
    
    def __init__(self, state: "State"):
        self._s = state
        self._suggestions = [
            "open <hive_file>",
            "ls", 
            "cd <path>",
            "info",
            "find <pattern>",
            "search <text>",
            "hex <value>",
            "note",
            "report",
            "recover",
            "models",
            "model",
            "config",
            "help",
        ]
        
    def get_suggestion(self, buffer, document):
        """Return a suggestion based on current context."""
        return self._get_suggestion_sync(document)
        
    async def get_suggestion_async(self, buffer, document):
        """Async version of get_suggestion."""
        return self._get_suggestion_sync(document)
        
    def _get_suggestion_sync(self, document):
        """Sync implementation for suggestion logic."""
        text = document.text_before_cursor.strip()
        
        if not text:
            # No input yet, suggest a random useful command
            if self._s.hive_open:
                suggestions = ["ls", "info", "cd", "find", "search", "note", "report"]
            else:
                suggestions = ["open <hive_file>", "help", "config"]
            return Suggestion(random.choice(suggestions))
            
        # Get first word to determine command
        words = text.split()
        if not words:
            return None
            
        cmd = words[0].lower()
        
        # Suggest based on partial command
        if len(cmd) < 3:
            matches = [s for s in self._suggestions if s.startswith(cmd)]
            if matches:
                return Suggestion(matches[0])
                
        # Context-aware suggestions
        if cmd in ("cd", "ls") and self._s.hive_open:
            if len(words) == 1:
                return Suggestion(f"{cmd} \\Software\\")
            elif len(words) == 2 and not text.endswith(" "):
                # Suggest common paths
                return Suggestion(f"{text}\\")
                
        elif cmd == "open" and len(words) == 1:
            return Suggestion("open C:\\Windows\\System32\\config\\SOFTWARE")
            
        elif cmd == "find" and len(words) == 1:
            return Suggestion("find Software")
            
        elif cmd == "search" and len(words) == 1:
            return Suggestion("search ")
            
        elif cmd == "hex" and len(words) == 1:
            return Suggestion("hex ")
            
        return None

# ── Textual REPL App ──────────────────────────────────────────────────────────

class Repl(App):
    """Textual-based REPL with Claude Code interface."""
    
    CSS = """
    Screen {
        layout: vertical;
    }
    
    .main-container {
        height: 100%;
        padding: 0;
    }
    
    .output-area {
        height: 1fr;
        padding: 0 1;
        background: $background;
        border: none;
    }
    
    .input-container {
        height: 3;
        padding: 0 1;
        background: $background;
        border: none;
    }
    
    .line {
        height: 1;
        padding: 0 1;
        background: $background;
        border: none;
        content-align: center middle;
    }
    
    ClaudeCodeInput {
        width: 100%;
        border: none;
        background: $background;
    }
    
    ClaudeCodeInput.-focus {
        border: none;
        background: $background;
    }
    """
    
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("enter", "submit_command", "Submit"),
        Binding("tab", "autocomplete", "Complete"),
    ]
    
    def __init__(self, config: dict, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.st = State()
        self.st.config = config
        self.ctrl_c_count = 0
        
        # Apply AI config
        self._apply_ai_config()
        
        # Initialize other components
        self._editor = Editor()
        self._config_panel = ConfigPanel()
        
    def compose(self) -> ComposeResult:
        """Compose the UI."""
        yield Header(show_clock=False)
        
        with Container(classes="main-container"):
            yield OutputArea(classes="output-area", id="output")
            yield ClaudeCodeLine(classes="line")
            with Container(classes="input-container"):
                yield ClaudeCodeInput(id="input")
            yield ClaudeCodeLine(classes="line")
            
        yield Footer()
        
    def on_mount(self) -> None:
        """Called when app is mounted."""
        self.title = "regbroker - Windows Registry Hive Forensics"
        self.sub_title = "AI-powered Analysis"
        
        # Focus on input
        input_widget = self.query_one("#input", ClaudeCodeInput)
        input_widget.focus()
        
        # Show welcome message
        self._show_banner()
        
    def _show_banner(self) -> None:
        """Show welcome banner."""
        output = self.query_one("#output", OutputArea)
        banner = """
[bold blue]regbroker[/bold blue] - Windows Registry Hive Forensics
[dim]AI-powered Registry Analysis Tool[/dim]

Type [cyan]help[/cyan] for commands or [cyan]open <hive_file>[/cyan] to start.
        """.strip()
        output.add_output(banner)
        
    def _apply_ai_config(self) -> None:
        """Apply AI configuration."""
        api_key = self.st.config.get("api_key", "")
        model = self.st.config.get("model", "anthropic/claude-3.5-haiku")
        if api_key:
            if self.st.ai:
                self.st.ai.set_model(model)
                self.st.ai.api_key = api_key
            else:
                self.st.ai = OpenRouterClient(api_key, model)
            
    def action_submit_command(self) -> None:
        """Handle command submission."""
        input_widget = self.query_one("#input", ClaudeCodeInput)
        command = input_widget.value.strip()
        
        if not command:
            return
            
        # Show command in output
        output = self.query_one("#output", OutputArea)
        output.add_output(f"> {command}", "cyan")
        
        # Clear input
        input_widget.value = ""
        
        # Reset Ctrl+C counter
        self.ctrl_c_count = 0
        
        # Process command
        asyncio.create_task(self._process_command(command))
        
    async def _process_command(self, command: str) -> None:
        """Process a command asynchronously."""
        output = self.query_one("#output", OutputArea)
        
        try:
            # Tokenize command
            tokens = _tok(command)
            if not tokens:
                return
                
            cmd, *args = tokens
            
            # Dispatch command
            await self._dispatch(cmd.lower(), args)
                
        except Exception as e:
            output.add_output(f"Error: {e}", "red")
            
    async def _dispatch(self, cmd: str, args: list[str]) -> None:
        """Dispatch command with all original functionality."""
        output = self.query_one("#output", OutputArea)
        
        try:
            {
                "open":    lambda: self._open(args),
                "ls":      lambda: self._ls(args),
                "dir":     lambda: self._ls(args),
                "cd":      lambda: self._cd(args),
                "info":    lambda: self._info(args),
                "hex":     lambda: self._hex(args),
                "find":    lambda: self._find(args),
                "search":  lambda: self._search(args),
                "note":    lambda: self._note(args),
                "report":  lambda: self._report(args),
                "recover": lambda: self._recover(args),
                "models":  lambda: self._models(args),
                "model":   lambda: self._model(args),
                "config":  lambda: self._config(args),
                "help":    lambda: self._help(args),
                "exit":    lambda: self._exit(args),
                "quit":    lambda: self._exit(args),
            }[cmd]()
        except KeyError:
            output.add_output(f"Unknown command: {cmd}", "red")
        except Exception as e:
            output.add_output(f"Error: {e}", "red")
            
    def action_autocomplete(self) -> None:
        """Handle autocomplete."""
        input_widget = self.query_one("#input", ClaudeCodeInput)
        current_text = input_widget.value
        
        # Simple autocomplete suggestions
        suggestions = ["help", "open", "ls", "cd", "info", "find", "search", "hex", "note", "report", "config", "exit"]
        
        for suggestion in suggestions:
            if suggestion.startswith(current_text):
                input_widget.value = suggestion
                break
                
    def action_quit(self) -> None:
        """Handle quit action."""
        if self.ctrl_c_count == 0:
            self.ctrl_c_count = 1
            output = self.query_one("#output", OutputArea)
            output.add_output("Press Ctrl+C again to exit", "yellow")
        else:
            self.exit()

    # ── Commands (same as original but with Textual output) ───────────────────────────

    def _open(self, args: list[str]) -> None:
        output = self.query_one("#output", OutputArea)
        if not args:
            output.add_output("Usage: open <hive_file>", "red")
            return
        hive_path = Path(args[0]).expanduser().resolve()
        if not hive_path.is_file():
            output.add_output(f"File not found: {hive_path}", "red")
            return
        try:
            self.st.hive_path = str(hive_path)
            self.st.hive_name = hive_path.name
            self.st.hive_info = bridge.info(self.st.hive_path)
            self.st.hive_open = True
            self.st.path = "\\"
            output.add_output(f"Opened: {self.st.hive_name}", "green")
            output.add_output(f"Type: {self.st.hive_info.get('type', 'unknown')}")
            output.add_output(f"Modified: {self.st.hive_info.get('last_modified', 'unknown')}")
        except Exception as e:
            output.add_output(f"Failed to open hive: {e}", "red")

    def _ls(self, args: list[str]) -> None:
        output = self.query_one("#output", OutputArea)
        if not self.st.hive_open:
            output.add_output("No hive is open. Use 'open <hive_file>' first.", "red")
            return
        try:
            data = bridge.ls(self.st.hive_path, self.st.path)
            # Keys
            if data.get("keys"):
                output.add_output("Keys:")
                for k in data["keys"]:
                    ts = k.get("last_modified", "")
                    ts_str = _ts(ts) if ts else ""
                    output.add_output(f"  {k.get('name', '')} {ts_str}", "cyan")
            # Values
            if data.get("values"):
                output.add_output("Values:")
                for v in data["values"]:
                    output.add_output(f"  {v.get('name', '(Default)')} {_val_type(v.get('type', ''))}", "green")
        except Exception as e:
            output.add_output(f"Failed to list: {e}", "red")

    def _cd(self, args: list[str]) -> None:
        output = self.query_one("#output", OutputArea)
        if not self.st.hive_open:
            output.add_output("No hive is open. Use 'open <hive_file>' first.", "red")
            return
        if not args:
            output.add_output("Usage: cd <path>", "red")
            return
        target = args[0]
        if target == "..":
            parts = [p for p in self.st.path.split("\\") if p]
            if len(parts) > 1:
                parts.pop()
                self.st.path = "\\" + "\\".join(parts)
            else:
                self.st.path = "\\"
        elif target.startswith("\\"):
            self.st.path = target
        else:
            self.st.path = self.st.path.rstrip("\\") + "\\" + target
        try:
            bridge.ls(self.st.hive_path, self.st.path)  # validate
            output.add_output(f"Changed to: {self.st.path}", "green")
        except Exception:
            output.add_output(f"Path not found: {self.st.path}", "red")
            # rollback
            parts = [p for p in self.st.path.split("\\") if p]
            if len(parts) > 1:
                parts.pop()
                self.st.path = "\\" + "\\".join(parts)
            else:
                self.st.path = "\\"

    def _info(self, args: list[str]) -> None:
        output = self.query_one("#output", OutputArea)
        if not self.st.hive_open:
            output.add_output("No hive is open. Use 'open <hive_file>' first.", "red")
            return
        try:
            data = bridge.ls(self.st.hive_path, self.st.path)
            output.add_output(f"Path: {self.st.path}")
            output.add_output(f"Keys: {len(data.get('keys', []))}")
            output.add_output(f"Values: {len(data.get('values', []))}")
            if self.st.path == "\\":
                output.add_output(f"Hive name: {self.st.hive_name}")
                output.add_output(f"Hive type: {self.st.hive_info.get('type', 'unknown')}")
                output.add_output(f"Last modified: {self.st.hive_info.get('last_modified', 'unknown')}")
        except Exception as e:
            output.add_output(f"Failed to get info: {e}", "red")

    def _hex(self, args: list[str]) -> None:
        output = self.query_one("#output", OutputArea)
        if not self.st.hive_open:
            output.add_output("No hive is open. Use 'open <hive_file>' first.", "red")
            return
        if not args:
            output.add_output("Usage: hex <value_name>", "red")
            return
        try:
            val = bridge.get(self.st.hive_path, self.st.path, args[0])
            output.add_output(f"Value: {args[0]}")
            output.add_output(f"Type: {val.get('type', 'unknown')}")
            output.add_output("Data:")
            data = val.get('data', b'')
            if isinstance(data, str):
                data = data.encode('utf-8', errors='replace')
            hex_str = data.hex()
            # format in blocks of 16 bytes
            for i in range(0, len(hex_str), 32):
                block = hex_str[i:i+32]
                ascii_block = data[i:i+16].decode('ascii', errors='replace')
                output.add_output(f"  {block:32} {ascii_block}")
        except Exception as e:
            output.add_output(f"Failed to read value: {e}", "red")

    def _find(self, args: list[str]) -> None:
        output = self.query_one("#output", OutputArea)
        if not self.st.hive_open:
            output.add_output("No hive is open. Use 'open <hive_file>' first.", "red")
            return
        if not args:
            output.add_output("Usage: find <pattern>", "red")
            return
        pattern = args[0]
        try:
            results = bridge.find(self.st.hive_path, pattern)
            if not results:
                output.add_output(f"No matches for: {pattern}")
                return
            output.add_output(f"Matches for: {pattern}")
            for r in results[:20]:  # limit output
                path = r.get('path', '')
                typ = r.get('type', 'unknown')
                output.add_output(f"  {path} ({typ})", "cyan")
            if len(results) > 20:
                output.add_output(f"... and {len(results)-20} more")
        except Exception as e:
            output.add_output(f"Failed to search: {e}", "red")

    def _search(self, args: list[str]) -> None:
        output = self.query_one("#output", OutputArea)
        if not self.st.hive_open:
            output.add_output("No hive is open. Use 'open <hive_file>' first.", "red")
            return
        if not args:
            output.add_output("Usage: search <text>", "red")
            return
        text = args[0]
        try:
            results = bridge.search(self.st.hive_path, text)
            if not results:
                output.add_output(f"No matches for: {text}")
                return
            output.add_output(f"Matches for: {text}")
            for r in results[:20]:
                path = r.get('path', '')
                value = r.get('value', '')
                output.add_output(f"  {path} {value}", "cyan")
                output.add_output(f"  {value}", "green")
            if len(results) > 20:
                output.add_output(f"... and {len(results)-20} more")
        except Exception as e:
            output.add_output(f"Failed to search: {e}", "red")

    def _note(self, args: list[str]) -> None:
        output = self.query_one("#output", OutputArea)
        note_text = " ".join(args) if args else None
        try:
            self._editor.edit(note_text)
            output.add_output("Note editor opened")
        except Exception as e:
            output.add_output(f"Failed to open editor: {e}", "red")

    def _report(self, args: list[str]) -> None:
        output = self.query_one("#output", OutputArea)
        if not self.st.hive_open:
            output.add_output("No hive is open. Use 'open <hive_file>' first.", "red")
            return
        if not self.st.ai:
            output.add_output("AI client not configured. Use 'config' first.", "red")
            return
        try:
            from .ai.report import generate_report
            self.st.report_md = generate_report(self.st)
            output.add_output("Report generated successfully!", "green")
            output.add_output(f"Saved to: {self.st.report_path}")
        except Exception as e:
            output.add_output(f"Failed to generate report: {e}", "red")
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
