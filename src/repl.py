from __future__ import annotations

import sys
import os
import shutil
from hive_utils import HiveUtils
from pathlib import Path
from typing import Optional

def get_char():
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getch()
        # Handle special keys (arrows, function keys, etc.)
        if ch in (b'\x00', b'\xe0'):
            # This is a special key, return the prefix character
            return ch.decode('latin1')
        else:
            # Regular character
            try:
                return ch.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    return ch.decode('latin1')
                except:
                    return ""
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch

def clear_screen():
    sys.stdout.write("\033[2J")   # clear
    sys.stdout.write("\033[H")    # cursor to top

def move_cursor(row, col):
    sys.stdout.write(f"\033[{row};{col}H")

def flush():
    sys.stdout.flush()

try:
    from . import config as cfg_mod
    from .ai.openrouter import OpenRouterClient, OpenRouterError
    from .tui.tree_nav import TreeNavigator
    from .tui.config_panel import ConfigPanel
    from .tui.editor import Editor
except ImportError:
    import config as cfg_mod
    from ai.openrouter import OpenRouterClient, OpenRouterError
    try:
        from tui.tree_nav import TreeNavigator
        from tui.config_panel import ConfigPanel
        from tui.editor import Editor
    except ImportError:
        TreeNavigator = None
        ConfigPanel = None
        Editor = None


class Repl(HiveUtils):   
    def __init__(self, config: dict):
        super().__init__()

        self.path = ""
        self.config = config
        self.st = State()
        self.st.config = config
        self.suggestions = [
            "load <hive_file>",
            "cd", 
            "info",
            "find <pattern>",
            "search <text>",
            "hex <value>",
            "note",
            "report",
            "recover",
            "models",
            "config",
            "tree",
            "edit",
            "help",
            "exit"
        ]
        
        self.buffer = ""
        self.output_lines = []
        self.cursor_pos = 0
        self.ctrl_c_count = 0
        self.ctrl_c_timer = 0
    
        
        # Path navigation state
        self.current_path = ""
        self.path_suggestions = []
        self.selected_suggestion = 0
        self.showing_suggestions = False

        # Terminal size tracking
        self.last_terminal_size = shutil.get_terminal_size((80, 24))
        
        # Streaming suggestions state
        self.total_suggestions_found = 0
        self.all_keys_loaded = False
        self.current_keys_iterator = None

        # Apply AI config
        self._apply_ai_config()
        

    def _apply_ai_config(self) -> None:
        api_key = self.st.config.get("api_key", "")
        model = self.st.config.get("model", "anthropic/claude-3.5-haiku")
        if api_key:
            self.st.ai = OpenRouterClient(api_key, model)
    
    def _has_children(self, path: str) -> bool:
        """Check if a path has subkeys"""
        if not self._hives:
            return False
            
        for hive_path, hive in self._hives.items():
            root = self._resolve_root(hive_path)
            try:
                # Remove the root from path if present
                clean_path = path
                if clean_path.startswith(root):
                    clean_path = clean_path[len(root):]
                if clean_path.startswith("\\"):
                    clean_path = clean_path[1:]
                    
                # Get the key and check for subkeys
                key = hive.get_key(clean_path)
                return len(key.subkeys) > 0
            except:
                continue
        return False
    
    def _get_suggestion(self) -> str:
        if not self.buffer:
            return ""
            
        # Check if buffer contains "cd " command for path suggestions
        if self.buffer.startswith("cd ") and len(self.buffer) > 3:
            partial_path = self.buffer[3:]  # Get everything after "cd "
            
            # Get path suggestions from current directory (streaming)
            # Check if we already have suggestions for this path
            current_partial_path = partial_path
            already_loaded = (
                self.path_suggestions and 
                self.showing_suggestions and
                hasattr(self, '_last_partial_path') and
                self._last_partial_path == current_partial_path
            )
            
            if not already_loaded:
                success, keys = self.list_keys(self.current_path)
                if success and keys:
                    # Clear previous suggestions to free memory
                    self.path_suggestions = []
                    self.current_keys_iterator = iter(keys)  # Store iterator for streaming
                    self._last_partial_path = current_partial_path
                    
                    # Stream suggestions - only load what we need
                    first_suggestion = None
                    suggestion_count = 0
                    max_initial_load = 20  # Load only first 20 initially
                    
                    for key in self.current_keys_iterator:
                        display_name = key.get("name", key["path"].split("\\")[-1])
                        if display_name.lower().startswith(partial_path.lower()):
                            suggestion = {
                                "display": display_name,
                                "path": key["path"],
                                "name": display_name
                            }
                            
                            if first_suggestion is None:
                                first_suggestion = suggestion["display"][len(partial_path):]
                            
                            self.path_suggestions.append(suggestion)
                            suggestion_count += 1
                            
                            # Stop after loading initial batch
                            if suggestion_count >= max_initial_load:
                                break
                    
                    if self.path_suggestions:
                        self.showing_suggestions = True
                        self.selected_suggestion = 0
                        self.total_suggestions_found = suggestion_count
                        # Don't set all_keys_loaded yet - we may have more
                        return first_suggestion or ""
            else:
                # Suggestions already loaded, just return the first one for autocomplete
                if self.path_suggestions:
                    self.showing_suggestions = True
                    first_suggestion = self.path_suggestions[0]["display"]
                    return first_suggestion[len(partial_path):] if first_suggestion.startswith(partial_path) else ""
        
        # Simple prefix matching for commands
        for suggestion in self.suggestions:
            if suggestion.startswith(self.buffer):
                remaining = suggestion[len(self.buffer):]
                return remaining
        return ""
    
    def _load_more_suggestions(self, partial_path: str, target_count: int) -> bool:
        """Load more suggestions on demand to reach target_count."""
        if self.all_keys_loaded or not self.current_keys_iterator:
            return False
        
        current_count = len(self.path_suggestions)
        while current_count < target_count:
            try:
                key = next(self.current_keys_iterator)
                display_name = key.get("name", key["path"].split("\\")[-1])
                if display_name.lower().startswith(partial_path.lower()):
                    suggestion = {
                        "display": display_name,
                        "path": key["path"],
                        "name": display_name
                    }
                    self.path_suggestions.append(suggestion)
                    current_count += 1
            except StopIteration:
                self.all_keys_loaded = True
                break
        
        return current_count > len(self.path_suggestions) - target_count
        
    def _open_tree_navigator(self) -> None:
        """Open tree navigator if available."""
        if TreeNavigator is None:
            self.output_lines.append("Tree navigator not available")
            return
            
        if not self.hive_load:
            self.output_lines.append("No hive loaded - use 'load <file>' first")
            return
            
        try:
            # Clear screen for TUI
            clear_screen()
            
            # Open tree navigator
            navigator = TreeNavigator(self.hive_path, self.path)
            selected_path = navigator.run()
            
            if selected_path:
                self.path = selected_path
                self.output_lines.append(f"Selected: {selected_path}")
            else:
                self.output_lines.append("Tree navigation cancelled")
                
        except Exception as e:
            self.output_lines.append(f"Tree navigator error: {e}")
    
    def _open_editor(self, file_path: Optional[str] = None) -> None:
        """Open text editor if available."""
        if Editor is None:
            self.output_lines.append("Editor not available")
            return
            
        try:
            # Clear screen for TUI
            clear_screen()
            
            # Open editor
            editor = Editor()
            if file_path:
                saved = editor.run(file_path)
                if saved:
                    self.output_lines.append(f"File saved: {file_path}")
                else:
                    self.output_lines.append("Editor closed without saving")
            else:
                self.output_lines.append("Editor opened - use Ctrl+S to save")
                editor.run("untitled.txt")
                
        except Exception as e:
            self.output_lines.append(f"Editor error: {e}")
    
    def _open_config_panel(self) -> None:
        """Open configuration panel if available."""
        if ConfigPanel is None:
            self.output_lines.append("Configuration panel not available")
            return
            
        try:
            # Clear screen for TUI
            clear_screen()
            
            # Open config panel
            panel = ConfigPanel()
            saved = panel.run(self.config)
            
            if saved:
                self.output_lines.append("Configuration saved")
                # Reload AI config
                self._apply_ai_config()
            else:
                self.output_lines.append("Configuration closed without saving")
                
        except Exception as e:
            self.output_lines.append(f"Configuration panel error: {e}")
    
    def _execute_command(self, command: str) -> None:
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
                    "  load <hive>      - Load a Windows Registry hive file",
                    "  unload <hive>    - Unload a Windows Registry hive file",
                    "  cd [path]        - List directory contents or navigate to path",
                    "  info             - Show current key information",
                    "  find <pattern>   - Find registry keys/values by name",
                    "  search <text>    - Search in registry values",
                    "  hex <value>      - Show value in hex format",
                    "  note             - Add forensic note",
                    "  report           - Generate AI-powered forensic report",
                    "  config           - Configure API settings",
                    "  tree             - Open tree navigator (TUI)",
                    "  edit [file]      - Open text editor (TUI)",
                    "  help             - Show this help",
                    "  exit             - Exit the application",
                    "",
                    "Navigation:",
                    "  cd               - List current directory contents",
                    "  cd <partial>    - Type partial path for autocomplete suggestions",
                    "  cd <full_path>  - Navigate directly to specified path",
                    "  Use arrow keys to select suggestions, TAB to accept",
                    "  Current path is shown as Current: <path>"
                ])
                
            elif cmd == "exit" or cmd == "quit":
                self.output_lines.append("Exiting...")
                return True  # Signal to exit
                
            elif cmd == "load" and args:
                hive_file = args[0]
                
                success, message = self.load_hive(hive_file)
                self.output_lines.append(message)
                
            elif cmd == "unload" and args:
                alias: str | None = args[0] if args else None
                
                if not self.unload_hive(alias):
                    self.output_lines.append(f"Failed to unload hive file: {alias}")
                    return
                
                self.output_lines.append(f"Unloaded hive file: {alias}")
                
            elif cmd == "cd":
                if not args:
                    # List current directory contents
                    success, keys = self.list_keys(self.current_path)
                    if success:
                        if keys:
                            for key in keys:
                                display_name = key.get("name", key["path"].split("\\")[-1])
                                self.output_lines.append(f"  {display_name}")
                        else:
                            self.output_lines.append("No subkeys found in current path")
                            if self.current_path:
                                self.output_lines.append(f"Current path: {self.current_path}")
                    else:
                        self.output_lines.append("No hives loaded")
                else:
                    # Navigate to specified path
                    target_path = " ".join(args)
                    
                    # Try to find the path in loaded hives
                    found_path = None
                    for hive_path, hive in self._hives.items():
                        root = self._resolve_root(hive_path)
                        try:
                            # Check if path exists
                            if target_path.startswith(root):
                                clean_path = target_path[len(root):]
                                if clean_path.startswith("\\"):
                                    clean_path = clean_path[1:]
                            else:
                                clean_path = target_path
                                
                            hive.get_key(clean_path)
                            found_path = target_path
                            break
                        except RegistryKeyNotFoundException:
                            continue
                    
                    if found_path:
                        self.current_path = found_path
                        self.output_lines.append(f"Changed to: {found_path}")
                    else:
                        self.output_lines.append(f"Path not found: {target_path}")
                    
            elif cmd == "ls-hives":
                self.output_lines.append(self.list_loaded_hives())

            elif cmd == "tree":
                self._open_tree_navigator()
                
            elif cmd == "edit":
                self._open_editor(args[0] if args else None)
                
            elif cmd == "config":
                self._open_config_panel()
                
            else:
                self.output_lines.append(f"Unknown command: {cmd}")
                self.output_lines.append("Type 'help' for available commands")
                
        except Exception as e:
            self.output_lines.append(f"Error: {e}")
            
        return False  # Continue running
            
    def render(self, full_render: bool = False) -> None:
        current_terminal_size = shutil.get_terminal_size((80, 24))
        terminal_height = current_terminal_size.lines
        terminal_width = current_terminal_size.columns
        line = "─" * terminal_width
        
        # Check for terminal resize
        if current_terminal_size != self.last_terminal_size:
            self.last_terminal_size = current_terminal_size
            full_render = True  # Force full render on resize
        
        # Calculate component heights
        suggestions_height = 0
        if self.showing_suggestions and self.path_suggestions:
            suggestions_height = min(10, len(self.path_suggestions))
        
        # Command component height: 3 lines (top frame, input, bottom frame)
        command_height = 3
        
        # Available space for output
        output_height = terminal_height - command_height - suggestions_height
        
        # Calculate pagination for suggestions with lazy loading
        suggestions_page_start = 0
        suggestions_page_end = min(10, len(self.path_suggestions))
        if self.showing_suggestions and self.path_suggestions:
            # Calculate which page to show based on selected_suggestion
            if self.selected_suggestion >= 10:
                suggestions_page_start = (self.selected_suggestion // 10) * 10
                suggestions_page_end = suggestions_page_start + 10
                
                # Load more suggestions if needed (lazy loading)
                if suggestions_page_end > len(self.path_suggestions) and not self.all_keys_loaded:
                    partial_path = self.buffer[3:] if self.buffer.startswith("cd ") else ""
                    self._load_more_suggestions(partial_path, suggestions_page_end)
                    suggestions_page_end = min(suggestions_page_start + 10, len(self.path_suggestions))
                
                suggestions_height = suggestions_page_end - suggestions_page_start
        
        if full_render:
            clear_screen()
            
            # Output component (top)
            current_row = 1
            for line_text in self.output_lines[-output_height:]:
                move_cursor(current_row, 1)
                sys.stdout.write(f"{line_text}")
                current_row += 1
            
            # Fill remaining output space
            while current_row <= output_height:
                move_cursor(current_row, 1)
                sys.stdout.write("")
                current_row += 1
            
            # Command component (middle)
            command_row = output_height + 1
            move_cursor(command_row, 1)
            sys.stdout.write(f"{line}")
            
            command_row += 1
            move_cursor(command_row, 1)
            sys.stdout.write(f"> {self.buffer}")
            
            suggestion = self._get_suggestion()
            if suggestion:
                sys.stdout.write(f"\033[90m{suggestion}\033[0m")
            
            command_row += 1
            move_cursor(command_row, 1)
            sys.stdout.write(f"{line}")
            
            # Suggestions component (bottom)
            if self.showing_suggestions and self.path_suggestions:
                suggestion_start_row = command_row + 1
                
                # Show ellipsis if there are suggestions before this page
                if suggestions_page_start > 0:
                    move_cursor(suggestion_start_row, 1)
                    sys.stdout.write("  ...")
                    suggestion_start_row += 1
                
                # Show current page of suggestions
                for i in range(suggestions_page_start, suggestions_page_end):
                    move_cursor(suggestion_start_row, 1)
                    suggestion = self.path_suggestions[i]
                    if i == self.selected_suggestion:
                        # Selected suggestion - white color (normal)
                        sys.stdout.write(f"  {suggestion['display']}")
                    else:
                        # Non-selected - gray color
                        sys.stdout.write(f"\033[90m  {suggestion['display']}\033[0m")
                    suggestion_start_row += 1
                
                # Show ellipsis if there are more suggestions after this page
                if suggestions_page_end < len(self.path_suggestions):
                    move_cursor(suggestion_start_row, 1)
                    sys.stdout.write("  ...")
        else:
            # Render rápido - atualiza componentes específicos
            
            # Calculate component positions and pagination with lazy loading
            suggestions_height = 0
            suggestions_page_start = 0
            suggestions_page_end = 0
            if self.showing_suggestions and self.path_suggestions:
                suggestions_height = min(10, len(self.path_suggestions))
                # Calculate pagination
                if self.selected_suggestion >= 10:
                    suggestions_page_start = (self.selected_suggestion // 10) * 10
                    suggestions_page_end = suggestions_page_start + 10
                    
                    # Load more suggestions if needed (lazy loading)
                    if suggestions_page_end > len(self.path_suggestions) and not self.all_keys_loaded:
                        partial_path = self.buffer[3:] if self.buffer.startswith("cd ") else ""
                        self._load_more_suggestions(partial_path, suggestions_page_end)
                        suggestions_page_end = min(suggestions_page_start + 10, len(self.path_suggestions))
                    
                    suggestions_height = suggestions_page_end - suggestions_page_start
                else:
                    suggestions_page_end = min(10, len(self.path_suggestions))
            
            command_height = 3
            output_height = terminal_height - command_height - suggestions_height
            command_row = output_height + 1
            
            # Update command component
            move_cursor(command_row + 1, 1)
            sys.stdout.write(" " * terminal_width)  # Clear input line
            move_cursor(command_row + 1, 1)
            sys.stdout.write(f"> {self.buffer}")
            
            suggestion = self._get_suggestion()
            if suggestion:
                sys.stdout.write(f"\033[90m{suggestion}\033[0m")
            
            # Update suggestions component if needed
            if self.showing_suggestions and self.path_suggestions:
                suggestion_start_row = command_row + 3
                
                # Clear all suggestion lines (max possible)
                for i in range(12):  # Clear up to 12 lines (10 suggestions + 2 ellipsis)
                    move_cursor(suggestion_start_row + i, 1)
                    sys.stdout.write(" " * terminal_width)  # Clear line
                
                # Show ellipsis if there are suggestions before this page
                if suggestions_page_start > 0:
                    move_cursor(suggestion_start_row, 1)
                    sys.stdout.write("  ...")
                    suggestion_start_row += 1
                
                # Show current page of suggestions
                for i in range(suggestions_page_start, suggestions_page_end):
                    move_cursor(suggestion_start_row, 1)
                    suggestion = self.path_suggestions[i]
                    if i == self.selected_suggestion:
                        # Selected suggestion - white color
                        sys.stdout.write(f"  {suggestion['display']}")
                    else:
                        # Non-selected - gray color
                        sys.stdout.write(f"\033[90m  {suggestion['display']}\033[0m")
                    suggestion_start_row += 1
                
                # Show ellipsis if there are more suggestions after this page
                if suggestions_page_end < len(self.path_suggestions):
                    move_cursor(suggestion_start_row, 1)
                    sys.stdout.write("  ...")
        
        # Position cursor correctly in command component
        suggestions_height = 0
        if self.showing_suggestions and self.path_suggestions:
            suggestions_height = min(10, len(self.path_suggestions))
        
        command_height = 3
        output_height = terminal_height - command_height - suggestions_height
        command_row = output_height + 1
        
        move_cursor(command_row + 1, 3 + self.cursor_pos)
        
        flush()
        
    def run(self) -> None:
        self.output_lines = [
            "regbroker - Windows Registry Hive Forensics",
            "AI-powered Registry Analysis Tool",
            "",
            "Type 'help' for commands or 'load <hive_file>' to start."
        ]
        
        self.render(full_render=True)
        
        # LOOP PRINCIPAL
        while True:
            ch = get_char()
    
            # CTRL+C
            if ch == "\x03":
                self.ctrl_c_count += 1
                if self.ctrl_c_count == 1:
                    self.output_lines.append("Press Ctrl+C again to exit")
                    self.render(full_render=True)

                    import time
                    self.ctrl_c_timer = time.time()
                elif self.ctrl_c_count >= 2:
                    current_time = time.time()
                    if current_time - self.ctrl_c_timer < 2.0:  # 2 segundos
                        clear_screen()
                        sys.exit(0)
                    else:
                        self.ctrl_c_count = 1
                        self.ctrl_c_timer = current_time
    
            # ENTER
            elif ch == "\r" or ch == "\n":
                if self.buffer.strip():
                    should_exit = self._execute_command(self.buffer.strip())
                    if should_exit:
                        break
                    self.render(full_render=True)
                self.buffer = ""
                self.cursor_pos = 0
                self.ctrl_c_count = 0
                self.ctrl_c_timer = 0
    
            # BACKSPACE
            elif ch == "\x7f" or ch == "\b":
                if self.cursor_pos > 0:
                    self.buffer = self.buffer[:self.cursor_pos-1] + self.buffer[self.cursor_pos:]
                    self.cursor_pos -= 1
    
            # Arrow keys and special keys
            elif ch in ("\x00", "\xe0"):  # Windows special key prefix
                # Get the actual key code
                try:
                    key_code = get_char()
                except:
                    continue
                    
                # Handle arrow keys and navigation keys
                if key_code == "\x4b" and self.cursor_pos > 0:  # Left arrow
                    self.cursor_pos -= 1
                    self.render(full_render=False)  # Update cursor position immediately
                elif key_code == "\x4d" and self.cursor_pos < len(self.buffer):  # Right arrow
                    self.cursor_pos += 1
                    self.render(full_render=False)  # Update cursor position immediately
                elif key_code == "\x47":  # Home key
                    self.cursor_pos = 0
                    self.render(full_render=False)  # Update cursor position immediately
                elif key_code == "\x4f":  # End key
                    self.cursor_pos = len(self.buffer)
                    self.render(full_render=False)  # Update cursor position immediately
                elif key_code == "\x73":  # Home key (alternative)
                    self.cursor_pos = 0
                    self.render(full_render=False)  # Update cursor position immediately
                elif key_code == "\x74":  # End key (alternative)
                    self.cursor_pos = len(self.buffer)
                    self.render(full_render=False)  # Update cursor position immediately
                elif key_code == "\x48":  # Up arrow
                    if self.path_suggestions:
                        if not self.showing_suggestions:
                            self.showing_suggestions = True
                        else:
                            self.selected_suggestion = max(0, self.selected_suggestion - 1)
                        self.render(full_render=False)
                elif key_code == "\x50":  # Down arrow
                    if self.path_suggestions:
                        if not self.showing_suggestions:
                            self.showing_suggestions = True
                        else:
                            self.selected_suggestion = min(len(self.path_suggestions) - 1, self.selected_suggestion + 1)
                        self.render(full_render=False)
                continue  # Skip processing as regular character
            elif ch == "\x1b":  # Unix/Linux escape sequence
                # Check for arrow sequence
                try:
                    next_ch = get_char()
                    if next_ch == "[":
                        arrow_ch = get_char()
                        if arrow_ch == "D" and self.cursor_pos > 0:  # Left
                            self.cursor_pos -= 1
                            self.render(full_render=False)  # Update cursor position immediately
                        elif arrow_ch == "C" and self.cursor_pos < len(self.buffer):  # Right
                            self.cursor_pos += 1
                            self.render(full_render=False)  # Update cursor position immediately
                        elif arrow_ch == "H":  # Home (Unix)
                            self.cursor_pos = 0
                            self.render(full_render=False)  # Update cursor position immediately
                        elif arrow_ch == "F":  # End (Unix)
                            self.cursor_pos = len(self.buffer)
                            self.render(full_render=False)  # Update cursor position immediately
                        elif arrow_ch == "A":  # Up
                            if self.path_suggestions:
                                if not self.showing_suggestions:
                                    self.showing_suggestions = True
                                else:
                                    self.selected_suggestion = max(0, self.selected_suggestion - 1)
                                self.render(full_render=False)
                        elif arrow_ch == "B":  # Down
                            if self.path_suggestions:
                                if not self.showing_suggestions:
                                    self.showing_suggestions = True
                                else:
                                    self.selected_suggestion = min(len(self.path_suggestions) - 1, self.selected_suggestion + 1)
                                self.render(full_render=False)
                except:
                    pass
                continue  # Skip processing as regular character

            # Regular character
            elif ch.isprintable():
                self.buffer = self.buffer[:self.cursor_pos] + ch + self.buffer[self.cursor_pos:]
                self.cursor_pos += 1
                
                # Only clear suggestions if the buffer structure changed significantly
                # Don't clear if we're just typing within the same "cd " command
                buffer_changed_significantly = (
                    not self.buffer.startswith("cd ") or 
                    len(self.buffer) < 3
                )
                
                if buffer_changed_significantly and self.showing_suggestions:
                    self.showing_suggestions = False
                    self.path_suggestions = []
                    self.selected_suggestion = 0
                    self.current_keys_iterator = None  # Free iterator
                    self.all_keys_loaded = False
                    self.total_suggestions_found = 0
                
                # Check for new suggestions after typing
                self._get_suggestion()
                self.render(full_render=False)  # Update display immediately
    
            # TAB for autocomplete
            elif ch == "\t":
                if self.showing_suggestions and self.path_suggestions:
                    # Accept selected suggestion
                    selected = self.path_suggestions[self.selected_suggestion]
                    
                    if self.buffer.startswith("cd "):
                        # Check if selected path has children
                        has_children = self._has_children(selected["path"])
                        
                        # Replace everything after "cd " with selected path
                        if has_children:
                            self.buffer = "cd " + selected["display"] + "/"
                        else:
                            self.buffer = "cd " + selected["display"]
                        
                        self.cursor_pos = len(self.buffer)
                        
                        # Clear suggestions
                        self.showing_suggestions = False
                        self.path_suggestions = []
                        self.selected_suggestion = 0
                        
                        # If has children, show new suggestions immediately
                        if has_children:
                            # Trigger new suggestions for the subdirectory
                            self._get_suggestion()  # This will populate new suggestions
                            self.render(full_render=False)
                else:
                    suggestion = self._get_suggestion()
                    if suggestion:
                        self.buffer = self.buffer + suggestion
                        self.cursor_pos = len(self.buffer)
    
            elif ch == "\x01":  # Home
                self.cursor_pos = 0
                self.render(full_render=False)  # Update cursor position immediately
    
            elif ch == "\x05":  # End
                self.cursor_pos = len(self.buffer)
                self.render(full_render=False)  # Update cursor position immediately

            self.render(full_render=False)
        
        # Clear screen on exit
        clear_screen()
        sys.stdout.write("Goodbye!\n")
        flush()


class State:  
    def __init__(self):
        self.hive_name: str = ""
        self.hive_path: str = ""
        self.hive_info: dict = {}
        self.path: str = "\\"
        self.hive_load: bool = False
        self.config: dict = {}
        self.ai: Optional[OpenRouterClient] = None
        self.model_cache: list = []
        self.report_md: str = ""
        self.report_path: Path = Path("laudo_pericial.md")


def run_repl(config: dict, initial_hive: str = "") -> None:
    repl = Repl(config)
    repl.run()
