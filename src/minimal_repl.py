"""Minimal REPL with Claude Code interface."""

from __future__ import annotations

import sys
import os
import shutil
from pathlib import Path
from typing import Optional

# =========================
# Cross-platform getch
# =========================
if os.name == "nt":
    import msvcrt
    def get_char():
        ch = msvcrt.getch()
        try:
            return ch.decode()
        except:
            return ""
else:
    import termios, tty
    def get_char():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch

# =========================
# ANSI helpers
# =========================
def clear_screen():
    sys.stdout.write("\033[2J")   # clear
    sys.stdout.write("\033[H")    # cursor to top

def move_cursor(row, col):
    sys.stdout.write(f"\033[{row};{col}H")

def flush():
    sys.stdout.flush()

try:
    from . import bridge, config as cfg_mod
    from .ai.openrouter import OpenRouterClient, OpenRouterError
except ImportError:
    import bridge
    import config as cfg_mod
    from ai.openrouter import OpenRouterClient, OpenRouterError


class MinimalRepl:
    """Minimal REPL with Claude Code interface."""
    
    def __init__(self, config: dict):
        self.config = config
        self.st = State()
        self.st.config = config
        self.suggestions = [
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
            "config",
            "help",
            "exit"
        ]
        
        # STATE (isso é o segredo)
        self.buffer = ""
        self.output_lines = []
        self.cursor_pos = 0
        
        # Apply AI config
        self._apply_ai_config()
        
    def _apply_ai_config(self) -> None:
        """Apply AI configuration."""
        api_key = self.st.config.get("api_key", "")
        model = self.st.config.get("model", "anthropic/claude-3.5-haiku")
        if api_key:
            self.st.ai = OpenRouterClient(api_key, model)
    
    def _get_suggestion(self) -> str:
        """Get auto-suggestion based on current input."""
        if not self.buffer:
            return ""
            
        # Simple prefix matching
        for suggestion in self.suggestions:
            if suggestion.startswith(self.buffer):
                remaining = suggestion[len(self.buffer):]
                return remaining
        return ""
        
    def _execute_command(self, command: str) -> None:
        """Execute a command."""
        self.output_lines.append(f"> {command}")
        
        try:
            parts = command.split()
            if not parts:
                return
                
            cmd = parts[0].lower()
            args = parts[1:] if len(parts) > 1 else []
            
            if cmd == "help":
                self.output_lines.extend([
                    "Available Commands:",
                    "  open <hive_file>  - Open a Windows Registry hive file",
                    "  ls               - List current registry keys and values",
                    "  cd <path>        - Change registry path",
                    "  info             - Show current key information",
                    "  find <pattern>   - Find registry keys/values by name",
                    "  search <text>    - Search in registry values",
                    "  hex <value>      - Show value in hex format",
                    "  note             - Add forensic note",
                    "  report           - Generate AI-powered forensic report",
                    "  config           - Configure API settings",
                    "  help             - Show this help",
                    "  exit             - Exit the application"
                ])
                
            elif cmd == "exit" or cmd == "quit":
                self.output_lines.append("Exiting...")
                return True  # Signal to exit
                
            elif cmd == "open" and args:
                hive_file = " ".join(args)
                self.output_lines.append(f"Opening hive file: {hive_file}")
                # TODO: Implement actual hive opening
                
            else:
                self.output_lines.append(f"Unknown command: {cmd}")
                self.output_lines.append("Type 'help' for available commands")
                
        except Exception as e:
            self.output_lines.append(f"Error: {e}")
            
        return False  # Continue running
            
    def render(self) -> None:
        """Render (igual React mental)"""
        clear_screen()
        
        terminal_height = shutil.get_terminal_size((80, 24)).lines
        terminal_width = shutil.get_terminal_size((80, 24)).columns
        line = "─" * terminal_width
        
        # Show output lines (keep space for frame)
        max_output_lines = terminal_height - 4
        for line_text in self.output_lines[-max_output_lines:]:
            sys.stdout.write(f"{line_text}\n")
        
        # Fill remaining space if needed
        remaining_lines = max_output_lines - len(self.output_lines)
        for _ in range(max(0, remaining_lines)):
            sys.stdout.write("\n")
        
        # Draw frame
        sys.stdout.write(f"{line}\n")
        sys.stdout.write(f"> {self.buffer}")
        
        # Show suggestion
        suggestion = self._get_suggestion()
        if suggestion:
            sys.stdout.write(f"\033[90m{suggestion}\033[0m")
        
        sys.stdout.write("\n")
        sys.stdout.write(f"{line}")
        
        # Coloca cursor depois do "> " (uma linha acima, uma coluna à frente)
        move_cursor(terminal_height - 2, 3 + len(self.buffer))
        
        flush()
        
    def run(self) -> None:
        """Run the minimal REPL."""
        # Show welcome message
        self.output_lines = [
            "regbroker - Windows Registry Hive Forensics",
            "AI-powered Registry Analysis Tool",
            "",
            "Type 'help' for commands or 'open <hive_file>' to start."
        ]
        
        # Initial render
        self.render()
        
        # LOOP PRINCIPAL
        while True:
            ch = get_char()
    
            # CTRL+C
            if ch == "\x03":
                break
    
            # ENTER
            elif ch == "\r" or ch == "\n":
                if self.buffer.strip():
                    should_exit = self._execute_command(self.buffer.strip())
                    if should_exit:
                        break
                self.buffer = ""
                self.cursor_pos = 0
    
            # BACKSPACE
            elif ch == "\x7f" or ch == "\b":
                if self.cursor_pos > 0:
                    self.buffer = self.buffer[:self.cursor_pos-1] + self.buffer[self.cursor_pos:]
                    self.cursor_pos -= 1
    
            # LEFT ARROW
            elif ch == "\x1b":
                # Check for arrow sequence
                if os.name != "nt":  # Unix systems
                    next_ch = get_char()
                    if next_ch == "[":
                        arrow_ch = get_char()
                        if arrow_ch == "D" and self.cursor_pos > 0:  # Left
                            self.cursor_pos -= 1
                        elif arrow_ch == "C" and self.cursor_pos < len(self.buffer):  # Right
                            self.cursor_pos += 1
    
            # Regular character
            elif ch.isprintable():
                self.buffer = self.buffer[:self.cursor_pos] + ch + self.buffer[self.cursor_pos:]
                self.cursor_pos += 1
    
            # TAB for autocomplete
            elif ch == "\t":
                suggestion = self._get_suggestion()
                if suggestion:
                    self.buffer = self.buffer + suggestion
                    self.cursor_pos = len(self.buffer)
    
            self.render()
        
        # Clear screen on exit
        clear_screen()
        sys.stdout.write("Goodbye!\n")
        flush()


class State:
    """REPL state management."""
    
    def __init__(self):
        self.hive_name: str = ""
        self.hive_path: str = ""
        self.hive_info: dict = {}
        self.path: str = "\\"
        self.hive_open: bool = False
        self.config: dict = {}
        self.ai: Optional[OpenRouterClient] = None
        self.model_cache: list = []
        self.report_md: str = ""
        self.report_path: Path = Path("laudo_pericial.md")


def run_minimal_repl(config: dict, initial_hive: str = "") -> None:
    """Run the minimal REPL."""
    repl = MinimalRepl(config)
    repl.run()


if __name__ == "__main__":
    # Test the minimal REPL
    test_config = {
        "api_key": "",
        "model": "anthropic/claude-3.5-haiku"
    }
    run_minimal_repl(test_config)
