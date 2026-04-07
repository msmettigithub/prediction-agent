"""Auto-discovers and loads all tool classes in the tools/ directory."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Optional

from tools.base_tool import BaseTool, ToolNameMismatchError

SKIP_MODULES = {"base_tool", "tool_registry", "__init__"}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def discover(self, mock_mode: bool = False):
        """Scan tools/ directory, import each module, find BaseTool subclasses, instantiate.

        Raises ToolNameMismatchError if any tool's .name property doesn't match its filename.
        """
        tools_dir = Path(__file__).parent
        for module_info in pkgutil.iter_modules([str(tools_dir)]):
            if module_info.name in SKIP_MODULES:
                continue
            module = importlib.import_module(f"tools.{module_info.name}")
            for attr_name, attr in inspect.getmembers(module, inspect.isclass):
                if issubclass(attr, BaseTool) and attr is not BaseTool:
                    try:
                        instance = attr(mock_mode=mock_mode)
                    except TypeError:
                        instance = attr()

                    # Fail fast: tool.name MUST match the filename it lives in
                    expected_name = module_info.name
                    actual_name = instance.name
                    if actual_name != expected_name:
                        raise ToolNameMismatchError(
                            f"Tool class {attr.__name__} in tools/{expected_name}.py has "
                            f"name='{actual_name}', expected name='{expected_name}'. "
                            f"Fix the @property name to return '{expected_name}'."
                        )

                    self._tools[instance.name] = instance

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def all(self) -> dict[str, BaseTool]:
        return dict(self._tools)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def health_check_all(self) -> list[dict]:
        results = []
        for name, tool in self._tools.items():
            try:
                result = tool.health_check()
                result["tool"] = name
            except Exception as e:
                result = {"tool": name, "healthy": False, "latency_ms": 0, "error": str(e)}
            results.append(result)
        return results
