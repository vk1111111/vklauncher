# vklauncher

A terminal UI Minecraft Java launcher for macOS, Windows and Linux.

- Fully isolated instances with each instance having its own `mods/`,
  `saves/`, `config/`, `resourcepacks/`, `options.txt`, etc.
- Download and launch any vanilla release or snapshot
- Support for downloading Fabric and Quilt versions.
- One-click install of Modrinth modpacks (`.mrpack`) into a new isolated
  instance with automatic mod-loader installation.
- Support for offline accounts and full Microsoft account sign-in.

## Requirements

- Python 3.10+
- A Java runtime installed (the launcher tries to auto-detect it via
  `JAVA_HOME` / `PATH`; you can also set an explicit path in Settings).
  Modern Minecraft (1.20.5+) needs Java 21, 1.17-1.20.4 needs Java 17,
  older versions need Java 8 so install whichever your target version needs.

## Install & run

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 main.py
```

## Keybindings (main screen)

| Key   | Action                        |
|-------|--------------------------------|
| n     | Create a new instance          |
| enter | Launch the selected instance   |
| d     | Delete the selected instance   |
| r     | Rename the selected instance   |
| a     | Manage accounts                |
| p     | Browse & install Modrinth packs|
| s     | Settings                       |
| q     | Quit                           |

Every screen shows its own keybindings at the bottom; `esc` goes back.

## Known limitations

- No support for Forge/NeoForge
- No built-in Java installer you need JDK already on your system.
- Resource/texture pack and shader browsing from Modrinth isn't built in
  (only modpacks)
