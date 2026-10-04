# Monsters & Memories — Linux Check

Pressing **Play** in the official Monsters & Memories Linux launcher (AppImage) and nothing happens? This tool checks everything the launcher needs to start the game, then tells you exactly what to do about anything that's missing, with commands for your distro and GPU.

![Check window](docs/screenshot.png)

## Use it

1. Download **`mnm-linux-check.py`** from the [latest release](../../releases/latest).
2. Double-click it, or run it in a terminal:

   ```sh
   python3 mnm-linux-check.py
   ```

3. Follow the numbered steps under **What to do**. Use **Copy** to copy a command, paste it into a terminal, then press **Check again**.

It never installs or changes anything by itself. The only button that writes files is **Apply launcher fix…**, and it asks first.

### Buttons

| Button | What it does |
| --- | --- |
| Check again | Re-runs every check. Read-only. |
| Test Proton | Runs a harmless Windows command through `umu-run` in the game's prefix, without starting the game. The first run may download GE-Proton (about 500 MB). |
| Watch for Play | Waits up to 10 minutes for you to press Play, then confirms the game really started. |
| Apply launcher fix… | Installs a small `umu-run` wrapper and launch script that stop the AppImage's environment from crashing `umu-run` (the "nothing happens" bug). Writes only to your home folder. |
| Choose AppImage… | Use this if your launcher AppImage isn't in `~/Applications`, `~/Downloads` or a similar folder. |

## What it checks

1. 64-bit x86 CPU
2. The launcher AppImage: found, executable, FUSE 2 installed
3. Game files (`mnm.exe` and core files) next to the AppImage
4. `umu-run`: on the launcher's `PATH`, actually runs, wrapper in place
5. GE-Proton, the Steam Linux Runtime, the Vulkan loader and a Vulkan driver for your GPU
6. The game's Wine prefix
7. Errors in the launcher's last log

## Requirements

- Python 3 with PyGObject and GTK 4 or 3. GNOME, Ubuntu, Fedora, Bazzite and SteamOS already have these. If yours doesn't, the tool prints the install command for your distro and runs the same check in the terminal.
- Terminal only: `python3 mnm-linux-check.py --cli` (supports `--test`, `--watch`, `--fix`, `--appimage PATH`), or run `mnm-linux-check.sh` with bash.

## Building

`mnm-linux-check.sh` holds the checks and `gui/mnm_check_gui.py` holds the window. Run `./build.sh` to combine them into the single file `dist/mnm-linux-check.py`.
