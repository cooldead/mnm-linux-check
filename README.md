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
| Watch for Play | Waits up to 10 minutes for you to press Play, then confirms the game really started, shows which GPU it's using, and, if it hangs, shows what Proton printed. |
| Apply launcher fix… | Installs a small `umu-run` wrapper and launch script that stop the AppImage's environment from crashing `umu-run` (the "nothing happens" bug). Writes only to your home folder. |
| Copy report | Copies the whole result as text, to paste into a [bug report](../../issues/new/choose) or Discord. It never includes your login token. |
| Choose AppImage… | Use this if your launcher AppImage isn't in `~/Applications`, `~/Downloads` or a similar folder. |

## What it checks

1. 64-bit x86 CPU
2. The launcher AppImage: found, executable, FUSE 2 installed
3. Game files (`mnm.exe` and core files) next to the AppImage
4. `umu-run`: on the launcher's `PATH`, actually runs, wrapper in place
5. GE-Proton, the Steam Linux Runtime, the Vulkan loader, a Vulkan driver for your GPU (and AMDVLK conflicts), and laptops with two GPUs
6. The game's Wine prefix
7. Errors in the launcher's last log
8. Python and PyGObject/GTK

## Requirements

Python 3 (`umu-run` needs it too) plus PyGObject with GTK 4 or 3 for the window. GNOME, Ubuntu, Linux Mint, Fedora, Bazzite and SteamOS usually have these already. If they're missing, the check shows the command for your distro, and the window falls back to the terminal check.

| Distro | Install command |
| --- | --- |
| Arch, CachyOS, Manjaro, EndeavourOS | `sudo pacman -S --needed python python-gobject gtk4` |
| Ubuntu, Linux Mint, Debian, Pop!_OS | `sudo apt install python3 python3-gi gir1.2-gtk-4.0` |
| Fedora, Nobara | `sudo dnf install python3 python3-gobject gtk4` |
| openSUSE | `sudo zypper install python3 python3-gobject-Gdk typelib-1_0-Gtk-4_0` |

### Laptops with two GPUs

On laptops with an integrated plus a discrete GPU, the game can start on the slow integrated GPU, or sit on "Game is running" with no window (NVIDIA Optimus). The check detects this, and **Apply launcher fix…** makes the game use the discrete GPU: PRIME render offload on NVIDIA, `DRI_PRIME=1` on AMD/Intel + AMD. To turn this off, set `MNM_NO_PRIME_OFFLOAD=1`.
- Terminal only: `python3 mnm-linux-check.py --cli` (supports `--test`, `--watch`, `--fix`, `--appimage PATH`), or run `mnm-linux-check.sh` with bash.

## Building

`mnm-linux-check.sh` holds the checks and `gui/mnm_check_gui.py` holds the window. Run `./build.sh` to combine them into the single file `dist/mnm-linux-check.py`.
