import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSPROJ = ROOT / "third_party" / "steamutility" / "SteamUtility.csproj"
WIN_OUT = ROOT / "libs" / "win-x64"
LINUX_OUT = ROOT / "libs" / "linux-x64"

PUBLISH_ARGS = [
    "-c",
    "Release",
    "--self-contained",
    "true",
    "-p:PublishSingleFile=false",
    "-p:PublishTrimmed=true",
    "-p:DebugType=none",
]


def _check_dotnet() -> None:
    """Require the .NET SDK (8.0) needed to publish SteamUtility."""
    try:
        subprocess.run(["dotnet", "--version"], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        sys.exit(
            ".NET SDK 8.0 is required to build SteamUtility. "
            f"Install it from https://dotnet.microsoft.com/download ({e})"
        )


def _publish(rid: str, out_dir: pathlib.Path) -> None:
    """Publish a self-contained SteamUtility bundle for the given RID."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    print(f"[+] Publishing SteamUtility {rid} -> {out_dir}...")
    subprocess.run(["dotnet", "publish", str(CSPROJ), "-r", rid, *PUBLISH_ARGS, "-o", str(out_dir)], check=True)


def main() -> None:
    """Build the win-x64 and linux-x64 SteamUtility bundles into their runtime locations."""
    _check_dotnet()
    _publish("win-x64", WIN_OUT)
    _publish("linux-x64", LINUX_OUT)

    # The vendored csproj's PostBuild target copies output into ../../src-tauri/... - an
    # SGI-repo layout that does not exist here. Remove the stray directory it creates.
    shutil.rmtree(ROOT / "src-tauri", ignore_errors=True)

    print("[+] Done. Windows libs -> libs/win-x64/, Linux libs -> libs/linux-x64/.")


if __name__ == "__main__":
    main()
