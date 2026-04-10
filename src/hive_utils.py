from typing import Optional
from regipy.registry import RegistryHive
from regipy.utils import convert_wintime
from regipy.plugins.utils import run_relevant_plugins
from regipy.exceptions import RegistryKeyNotFoundException, RegistryParsingException
import traceback

import logging
logging.getLogger("regipy").setLevel(logging.ERROR)

HIVE_ROOT_MAP = {
    "SYSTEM":   "HKLM\\SYSTEM",
    "SOFTWARE": "HKLM\\SOFTWARE",
    "SAM":      "HKLM\\SAM",
    "SECURITY": "HKLM\\SECURITY",
    "NTUSER.DAT": "HKCU",
    "UsrClass.dat": "HKCU\\Software\\Classes",
}


class HiveUtils:
    def __init__(self):
        self._hives: dict[str, RegistryHive] = {}

    def _resolve_root(self, path: str) -> str:
        filename = path.replace("\\", "/").split("/")[-1].upper()

        for key, root in HIVE_ROOT_MAP.items():
            if filename == key:
                return root
        
        return filename

    def load_hive(self, path: str) -> tuple[bool, str]:
        try:
            self._hives[path] = RegistryHive(path)
        except Exception as e:
            return False, f"Error loading hive file {path}: {e}"

        return True, f"Loaded hive file: {path}"

    def unload_hive(self, path: str) -> tuple[bool, str]:
        if path not in self._hives:
            return False, f"Hive {path} not found"

        del self._hives[path]
        return True, f"Unloaded hive file: {path}"

    def list_loaded_hives(self) -> list[str]:
        return list(self._hives.keys())

    def list_keys(self, current_path: str = "") -> tuple[bool, list]:
        if not self._hives:
            return False, []
        
        keys = []
        
        if not current_path:
            for path, hive in self._hives.items():
                root = self._resolve_root(path)
                for entry in hive.recurse_subkeys():
                    keys.append({
                        "hive": root,
                        "path": f"{root}\\{entry.path}",
                        "timestamp": entry.timestamp
                    })
        else:
            for path, hive in self._hives.items():
                root = self._resolve_root(path)
                try:
                    clean_path = current_path
                    if clean_path.startswith(root):
                        clean_path = clean_path[len(root):]
                    if clean_path.startswith("\\"):
                        clean_path = clean_path[1:]
                    
                    current_key = hive.get_key(clean_path)
                    
                    for subkey in current_key.subkeys:
                        keys.append({
                            "hive": root,
                            "path": f"{root}\\{subkey.path}",
                            "timestamp": subkey.timestamp,
                            "name": subkey.name
                        })
                        
                except RegistryKeyNotFoundException:
                    continue
                except Exception:
                    continue
        
        return True, keys
    
    def get_path_suggestions(self, partial_path: str, current_path: str = "") -> tuple[bool, list]:
        if not self._hives:
            return False, []
        
        suggestions = []
        
        if partial_path.startswith("/"):
            partial_path = partial_path[1:]  # Remove o '/'
            
            if current_path:
                full_path = current_path + "\\" + partial_path
            else:
                full_path = partial_path
                
            full_path = full_path.replace("\\\\", "\\")
            
            for path, hive in self._hives.items():
                root = self._resolve_root(path)
                try:
                    clean_path = full_path
                    if clean_path.startswith(root):
                        clean_path = clean_path[len(root):]
                    if clean_path.startswith("\\"):
                        clean_path = clean_path[1:]
                    
                    if "\\" in clean_path:
                        parent_path = clean_path.rsplit("\\", 1)[0]
                        search_prefix = clean_path.rsplit("\\", 1)[1].lower()
                    else:
                        parent_path = ""
                        search_prefix = clean_path.lower()
                    
                    parent_key = hive.get_key(parent_path) if parent_path else hive.root
                    
                    for subkey in parent_key.subkeys:
                        if subkey.name.lower().startswith(search_prefix):
                            suggestions.append({
                                "hive": root,
                                "path": f"{root}\\{subkey.path}",
                                "name": subkey.name,
                                "display": subkey.name
                            })
                            
                except RegistryKeyNotFoundException:
                    continue
                except Exception:
                    continue
        
        suggestions = suggestions[:10]
        
        return True, suggestions
