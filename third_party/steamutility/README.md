# SteamUtility (vendored source)

Vendored from [steam-game-idler](https://github.com/zevnda/steam-game-idler) at tag **6.2.2**
(`libs/SteamUtility/`), Elastic License 2.0 — see `LICENSE`.

This is the C# source for the SteamUtility executable bundled by Steam Play Manager.
It is published for both platforms with `scripts/build_steamutility.py`:

- `win-x64` (CLI + agent daemon, Steamworks.NET-backed) → `libs/win-x64/`
- `linux-x64` (agent daemon only — the csproj excludes the CLI/Steamworks surface via
  `RuntimeIdentifier != win-x64`) → `libs/linux-x64/`

To re-sync from a newer upstream tag: replace this directory with the new tag's
`libs/SteamUtility/` (keeping `LICENSE`), then run the build script and re-verify both
bundles. Do not edit the vendored code here — patch it upstream and re-vendor.
