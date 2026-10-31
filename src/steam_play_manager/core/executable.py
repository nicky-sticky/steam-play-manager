import sys
from pathlib import Path


def build_launch_command(exe: Path, dll: Path | None, *args: str) -> list[str]:
    """Return the command that runs a bundled .NET binary on this platform."""
    if sys.platform == "win32":
        return [str(exe), *args]
    # Non-Windows: a Windows-named .exe cannot be exec'd directly - run it via the dotnet
    # host when a framework-dependent dll is available. A self-contained Linux publish ships
    # an ELF apphost (no .exe suffix) that runs directly instead.
    if str(exe).lower().endswith(".exe") and dll is not None and dll.exists():
        return ["dotnet", str(dll), *args]
    return [str(exe), *args]
