#!/usr/bin/env bash
# mnm-linux-check.sh — checks that the Monsters & Memories Linux launcher (AppImage)
# can hand off to mnm.exe, prints the exact install commands for anything missing,
# and then confirms the launcher → mnm.exe link actually works.
#
# How the official launcher starts the game on Linux (read from its binary + log):
#   umu-run <AppImage dir>/mnm/mnm.exe --token …
#     WINEPREFIX=${MNM_WINEPREFIX:-${XDG_DATA_HOME:-~/.local/share}/mnm/mnm/pfx}
#     PROTONPATH=${MNM_PROTONPATH:-GE-Proton}   GAMEID=umu-monstersandmemories
# So the chain is: AppImage (needs FUSE 2) → `umu-run` found on the launcher's PATH →
# GE-Proton + Steam Linux Runtime (umu downloads both) → Vulkan driver → mnm.exe.
# Any broken link shows up as "nothing happens when I press Play".
#
# Usage:  bash mnm-linux-check.sh [options]
#   (none)            check everything, print fixes for anything missing
#   --test            also run a harmless command through umu-run in the game prefix
#   --watch           wait for you to press Play, then confirm mnm.exe came up via umu
#   --fix             install the env-cleaning umu-run wrapper + launch script (see below)
#   --appimage PATH   launcher AppImage, if it isn't in ~/Applications, ~/Downloads, …
#
# MNM_CHECK_GUI=1 switches output to tab-separated "@@kind\ttext" records for the GUI.
#
# Read-only unless --fix is given. Never prints the launcher's login token (it's on
# mnm.exe's command line) — only argv[0] of game processes is ever shown.

SELF=${MNM_CHECK_SELF:-bash $0}                # how to re-run this check, for the fix list
GUI=${MNM_CHECK_GUI:-0}
GAMEID=umu-monstersandmemories
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
PREFIX=${MNM_WINEPREFIX:-$DATA_HOME/mnm/mnm/pfx}
PROTON=${MNM_PROTONPATH:-GE-Proton}
MNM_HOME=$DATA_HOME/mnm                       # where --fix puts the wrapper/log
WRAP_DIR=$MNM_HOME/bin
LOG_DIR_GAME='drive_c/users/steamuser/AppData/LocalLow/Niche Worlds Cult/Monsters and Memories'

DO_TEST=0 DO_WATCH=0 DO_FIX=0 APPIMAGE_ARG=${MNM_LAUNCHER:-}
while [ $# -gt 0 ]; do
  case $1 in
    --test) DO_TEST=1 ;;
    --watch) DO_WATCH=1 ;;
    --fix) DO_FIX=1 ;;
    --appimage) APPIMAGE_ARG=$2; shift ;;
    --appimage=*) APPIMAGE_ARG=${1#*=} ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

# ── output helpers ────────────────────────────────────────────────────────────
if [ -t 1 ] && [ "$GUI" != 1 ]; then G=$'\e[32m' R=$'\e[31m' Y=$'\e[33m' B=$'\e[1m' D=$'\e[2m' N=$'\e[0m'; else G= R= Y= B= D= N=; fi
FAILS=0 WARNS=0
declare -a FIXES=()                            # numbered "do this" list printed at the end
if [ "$GUI" = 1 ]; then
section() { printf '@@section\t%s\n' "$1"; }
ok()   { printf '@@ok\t%s\n' "$1"; }
bad()  { printf '@@bad\t%s\n' "$1"; FAILS=$((FAILS+1)); }
warn() { printf '@@warn\t%s\n' "$1"; WARNS=$((WARNS+1)); }
info() { printf '@@info\t%s\n' "$1"; }
else
section() { printf '\n%s%s%s\n' "$B" "$1" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$R" "$N" "$1"; FAILS=$((FAILS+1)); }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; WARNS=$((WARNS+1)); }
info() { printf '  %s·%s %s\n' "$D" "$N" "$1"; }
fi
detail() { sed 's/^/        /'; }               # stdin → indented detail lines under the last item
# Strip personal data from text that goes into reports: home paths (Linux and Wine
# drive form), the launcher's login token, e-mail addresses and web tokens (JWTs).
scrub() {
  sed -E -e "s#${HOME//#/\\#}#~#g" \
    -e 's#/home/[^/ ]+#~#g; s#[A-Za-z]:\\home\\[^\\ ]+#~#g' \
    -e 's/(--token[= ])[^ ]+/\1<hidden>/g; s/(token|password|secret)=[^ &]+/\1=<hidden>/gI' \
    -e 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/<email>/g; s/eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9._-]+/<hidden>/g'
}
fix()  { local f; for f in "${FIXES[@]}"; do [ "$f" = "$1" ] && return; done; FIXES+=("$1"); }
have() { command -v "$1" >/dev/null 2>&1; }
tilde() { printf '%s' "${1/#$HOME/\~}"; }

# ── distro + GPU, so the fix commands are the right ones for this machine ─────
OS_ID= OS_LIKE= OS_NAME=Linux
[ -r /etc/os-release ] && . /etc/os-release && OS_ID=${ID:-} OS_LIKE=${ID_LIKE:-} OS_NAME=${PRETTY_NAME:-Linux}
case " $OS_ID $OS_LIKE " in
  *" arch "*|*" cachyos "*|*" manjaro "*|*" endeavouros "*) FAMILY=arch ;;
  *" fedora "*|*" rhel "*|*" nobara "*|*" bazzite "*) FAMILY=fedora ;;
  *" debian "*|*" ubuntu "*|*" pop "*|*" linuxmint "*) FAMILY=debian ;;
  *" suse "*|*" opensuse "*|*" opensuse-tumbleweed "*) FAMILY=suse ;;
  *) FAMILY=other ;;
esac
# Image-based distros (SteamOS, Bazzite, Silverblue…) can't just `pacman -S`/`dnf install`
IMMUTABLE=0
{ [ -e /run/ostree-booted ] || [ "$OS_ID" = steamos ]; } && IMMUTABLE=1

GPUS=""                                        # vendor ids from sysfs — no lspci needed
for v in /sys/class/drm/card*/device/vendor; do
  [ -r "$v" ] || continue
  case $(cat "$v") in 0x10de) GPUS="$GPUS nvidia" ;; 0x1002) GPUS="$GPUS amd" ;; 0x8086) GPUS="$GPUS intel" ;; esac
done
GPUS=$(printf '%s\n' $GPUS | sort -u | tr '\n' ' ')

pkg_cmd() {  # pkg_cmd <arch pkgs> <fedora pkgs> <debian pkgs> <suse pkgs>
  case $FAMILY in
    arch)   echo "sudo pacman -S --needed $1" ;;
    fedora) echo "sudo dnf install $2" ;;
    debian) echo "sudo apt install $3" ;;
    suse)   echo "sudo zypper install $4" ;;
    *)      echo "install with your package manager: $1 (Arch names)" ;;
  esac
}

UMU_INSTALL=$(case $FAMILY in
  arch)   echo "sudo pacman -S --needed umu-launcher" ;;
  fedora) echo "sudo dnf install umu-launcher" ;;
  debian) echo "download the umu-launcher .deb for your release from https://github.com/Open-Wine-Components/umu-launcher/releases and run: sudo apt install ./umu-launcher*.deb" ;;
  *)      echo "pipx install umu-launcher   (then make sure ~/.local/bin is on your PATH)" ;;
esac)
[ $IMMUTABLE = 1 ] && UMU_INSTALL="pipx install umu-launcher   (image-based distro — or use your distro's documented way to add umu-launcher)"

if [ "$GUI" = 1 ]; then printf '@@system\t%s\t%s\n' "$OS_NAME" "${GPUS:-unknown}"
else printf '%sMonsters & Memories — Linux launcher check%s  %s(%s; GPU:%s)%s\n' "$B" "$N" "$D" "$OS_NAME" "${GPUS:- unknown}" "$N"; fi

# ── 1. System ─────────────────────────────────────────────────────────────────
section "1. System"
[ "$(uname -m)" = x86_64 ] && ok "64-bit x86 CPU" || bad "CPU is $(uname -m) — the game and launcher are x86_64 only"

# ── 2. Launcher AppImage ──────────────────────────────────────────────────────
section "2. Launcher (AppImage)"
LAUNCHER_PID=$(pgrep -x mnm_launcher | head -1)
envof() { tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null | sed -n "s/^$2=//p" | head -1; }

APPIMAGE=""
if [ -n "$APPIMAGE_ARG" ]; then APPIMAGE=$APPIMAGE_ARG
elif [ -n "$LAUNCHER_PID" ]; then APPIMAGE=$(envof "$LAUNCHER_PID" APPIMAGE)
fi
if [ -z "$APPIMAGE" ]; then  # newest MonstersAndMemories*.appimage in the usual drop spots
  APPIMAGE=$(find "$HOME/Applications" "$HOME/Downloads" "$HOME/Desktop" "$HOME/Games" "$HOME/.local/bin" "$HOME" /opt \
               -maxdepth 1 -iname 'MonstersAndMemories*.appimage' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
fi

if [ -z "$APPIMAGE" ] || [ ! -f "$APPIMAGE" ]; then
  bad "Launcher AppImage not found${APPIMAGE:+ at $APPIMAGE}"
  fix "Download the Linux launcher (MonstersAndMemories_*.appimage) from the official Monsters & Memories site into ~/Applications, then re-run this check (or pass --appimage /path/to/it)"
  APPIMAGE=""
else
  ok "Found $(tilde "$APPIMAGE")"
  [ -n "$LAUNCHER_PID" ] && info "Launcher is running (pid $LAUNCHER_PID)"
  if [ -x "$APPIMAGE" ]; then ok "AppImage is executable"
  else bad "AppImage isn't marked executable"; fix "chmod +x '$APPIMAGE'"; fi
fi

# AppImages mount themselves with FUSE 2 (libfuse.so.2), which many distros no longer ship by default
if ldconfig -p 2>/dev/null | grep -q 'libfuse\.so\.2 ' || ls /usr/lib*/libfuse.so.2 /usr/lib/*/libfuse.so.2 >/dev/null 2>&1; then
  ok "FUSE 2 (libfuse.so.2) is installed — AppImages can mount"
elif [ -n "$LAUNCHER_PID" ]; then
  ok "FUSE 2 not detected, but the launcher is running anyway"
else
  bad "FUSE 2 (libfuse.so.2) missing — the AppImage won't open"
  fix "$(pkg_cmd fuse2 fuse-libs 'libfuse2t64   (Ubuntu 22.04/Debian 12 and older: libfuse2)' libfuse2)"
fi

# ── 3. Game files ─────────────────────────────────────────────────────────────
# The launcher installs into "mnm" next to the AppImage itself, so moving the AppImage
# makes it look like the game vanished.
section "3. Game install (mnm.exe)"
GAME_DIR=""
[ -n "$APPIMAGE" ] && GAME_DIR=$(dirname "$(readlink -f "$APPIMAGE")")/mnm
if [ -z "$GAME_DIR" ]; then
  info "Skipped — need the AppImage location first"
elif [ ! -f "$GAME_DIR/mnm.exe" ]; then
  bad "mnm.exe not found in $(tilde "$GAME_DIR")"
  fix "Open the launcher, log in and click Install — it downloads the game into $(tilde "$GAME_DIR") (keep the AppImage where it is)"
else
  if [ "$(head -c2 "$GAME_DIR/mnm.exe")" = MZ ]; then ok "mnm.exe present ($(tilde "$GAME_DIR"))"
  else bad "mnm.exe exists but isn't a Windows program (corrupt download?)"; fix "In the launcher, use Verify/Repair (or delete $(tilde "$GAME_DIR") and reinstall)"; fi
  missing=""
  for f in GameAssembly.dll UnityPlayer.dll mnm_Data; do [ -e "$GAME_DIR/$f" ] || missing="$missing $f"; done
  if [ -z "$missing" ]; then ok "Core game files present"
  else bad "Missing game files:$missing"; fix "In the launcher, let it update/verify the game (files missing:$missing)"; fi
fi

# ── 4. umu-run — the actual bridge between the launcher and mnm.exe ───────────
section "4. umu-run (launcher → Proton bridge)"

# Work out the PATH the *launcher* sees, which is not necessarily your terminal's PATH:
# started from the app menu it gets the desktop session's PATH (+ whatever its .desktop
# Exec script prepends). If it's running right now, just read its real environment.
DESKTOP_FILE="" DESKTOP_EXEC=""
if [ -n "$APPIMAGE" ]; then
  # The original entry names the AppImage; the one --fix creates names the launch script
  DESKTOP_FILE=$(grep -lsF -e "$(basename "$APPIMAGE")" -e "$MNM_HOME/mnm-launcher.sh" "$DATA_HOME"/applications/*.desktop | head -1)
  [ -n "$DESKTOP_FILE" ] && DESKTOP_EXEC=$(sed -n 's/^Exec=//p' "$DESKTOP_FILE" | head -1 | sed 's/ %[a-zA-Z]//g; s/^"\(.*\)"$/\1/')
fi
if [ -n "$LAUNCHER_PID" ]; then
  LPATH=$(envof "$LAUNCHER_PID" PATH); LAPPDIR=$(envof "$LAUNCHER_PID" APPDIR)
  [ -n "$LAPPDIR" ] && LPATH=$(printf '%s' "$LPATH" | tr ':' '\n' | grep -v "^$LAPPDIR" | paste -sd:)
  PATH_SRC="the running launcher"
else
  LPATH=$(systemctl --user show-environment 2>/dev/null | sed -n 's/^PATH=//p')
  PATH_SRC="your desktop session"
  [ -z "$LPATH" ] && LPATH=$PATH PATH_SRC="this shell"
  # A launch script in the .desktop Exec (like the one --fix writes) can prepend dirs
  if [ -n "$DESKTOP_EXEC" ] && [ -f "$DESKTOP_EXEC" ] && head -c2 "$DESKTOP_EXEC" | grep -q '#!'; then
    pre=$(sed -n 's/^ *export PATH="\{0,1\}\([^"]*\):\$PATH.*/\1/p' "$DESKTOP_EXEC" | head -1)
    pre=${pre//\$HOME/$HOME}; pre=${pre//\$\{HOME\}/$HOME}
    [ -n "$pre" ] && LPATH=$pre:$LPATH PATH_SRC="$PATH_SRC + $(tilde "$DESKTOP_EXEC")"
  fi
fi

UMU_ALL=()                                     # every umu-run on that PATH, in lookup order
IFS=: read -ra _dirs <<< "$LPATH"
declare -A _seen=()                           # /bin → /usr/bin symlinks would list one file twice
for d in "${_dirs[@]}"; do
  [ -n "$d" ] && [ -x "$d/umu-run" ] && [ ! -d "$d/umu-run" ] || continue
  r=$(readlink -f "$d/umu-run"); [ -n "${_seen[$r]:-}" ] && continue
  _seen[$r]=1; UMU_ALL+=("$d/umu-run")
done
UMU=${UMU_ALL[0]:-}
is_wrapper() { grep -qs 'unset PYTHONHOME' "$1" 2>/dev/null && grep -qs 'umu-run' "$1"; }

if [ -z "$UMU" ]; then
  if have umu-run; then
    bad "umu-run is installed ($(tilde "$(command -v umu-run)")) but NOT on the PATH of $PATH_SRC — the launcher can't see it"
    fix "Make the desktop session see it: mkdir -p ~/.config/environment.d && echo 'PATH=$(dirname "$(command -v umu-run)"):\${PATH}' >> ~/.config/environment.d/mnm.conf   then log out and back in"
  else
    bad "umu-run not installed — the launcher says \"umu-launcher not found\" and Play does nothing"
    fix "$UMU_INSTALL"
  fi
else
  ok "Launcher will use $(tilde "$UMU")  ${D}(PATH from $PATH_SRC)${N}"
  [ ${#UMU_ALL[@]} -gt 1 ] && info "Other umu-run copies further down PATH: $(for u in "${UMU_ALL[@]:1}"; do printf '%s ' "$(tilde "$u")"; done)"
  if is_wrapper "$UMU"; then
    ok "It's the env-cleaning wrapper (protects umu-run from the AppImage's leaked environment)"
    REAL=""; for u in "${UMU_ALL[@]:1}"; do is_wrapper "$u" || { REAL=$u; break; }; done
    [ -z "$REAL" ] && REAL=$(sed -n 's/^exec \([^ ]*umu-run\).*/\1/p' "$UMU" | head -1)
    if [ -z "$REAL" ] || [ ! -x "$REAL" ]; then
      bad "…but the wrapper can't find a real umu-run behind it"; fix "$UMU_INSTALL"
    fi
  fi
  # umu-run is Python — a broken Python install or a half-installed pipx copy fails here
  if v=$(timeout 30 "$UMU" --version 2>&1) && printf '%s' "$v" | grep -qi 'umu'; then
    ok "umu-run works: $(printf '%s' "$v" | grep -io 'umu-launcher version [0-9.]*' | head -1)"
  else
    bad "umu-run is on PATH but fails to run:"
    printf '%s\n' "$v" | tail -4 | sed 's/^/        /'
    fix "Reinstall umu-launcher: $UMU_INSTALL"
  fi
fi

# Launch scripts written by --fix before v1.1 matched only lowercase ".appimage"
OLD_LAUNCH=$MNM_HOME/mnm-launcher.sh
if [ -n "$APPIMAGE" ] && [ -f "$OLD_LAUNCH" ] && grep -qs 'ls -t .*MonstersAndMemories\*\.appimage' "$OLD_LAUNCH" \
   && ! ls "$(dirname "$APPIMAGE")"/MonstersAndMemories*.appimage >/dev/null 2>&1; then
  bad "$(tilde "$OLD_LAUNCH") (from an older --fix) only looks for *.appimage, so it can't find $(basename "$APPIMAGE")"
  fix "$SELF --fix   (rewrites the launch script so it finds .AppImage and .appimage)"
fi

# ── 5. Proton + runtime + Vulkan ──────────────────────────────────────────────
section "5. Proton, Steam Runtime, Vulkan"
if [ "$PROTON" = GE-Proton ]; then
  ge=$(ls -d "$DATA_HOME"/Steam/compatibilitytools.d/GE-Proton* "$HOME"/.steam/root/compatibilitytools.d/GE-Proton* 2>/dev/null | sort -V | tail -1)
  if [ -n "$ge" ]; then ok "GE-Proton downloaded ($(basename "$ge"))"
  else info "GE-Proton not downloaded yet — umu-run fetches it (~500 MB) on the first Play; the first start can take several minutes"; fi
elif [ -d "$PROTON" ]; then ok "Using custom Proton from MNM_PROTONPATH ($(tilde "$PROTON"))"
else bad "MNM_PROTONPATH=$PROTON is not a directory"; fix "Unset MNM_PROTONPATH, or point it at an unpacked Proton folder"; fi

if ls -d "$DATA_HOME"/umu/steamrt* >/dev/null 2>&1; then ok "Steam Linux Runtime present ($(ls -d "$DATA_HOME"/umu/steamrt* | xargs -n1 basename | tr '\n' ' '))"
else info "Steam Linux Runtime not downloaded yet — umu-run fetches it on the first Play"; fi

if ldconfig -p 2>/dev/null | grep -q 'libvulkan\.so\.1 .*x86-64'; then ok "Vulkan loader installed"
else
  bad "Vulkan loader (libvulkan.so.1) missing — Proton/DXVK can't draw anything"
  fix "$(pkg_cmd 'vulkan-icd-loader lib32-vulkan-icd-loader' vulkan-loader libvulkan1 libvulkan1)"
fi

ICDS=$(ls /usr/share/vulkan/icd.d/*.json /etc/vulkan/icd.d/*.json 2>/dev/null | xargs -rn1 basename | tr '\n' ' ')
for gpu in $GPUS; do
  case $gpu in
    nvidia) pat='nvidia' pkg=$(case $FAMILY in arch) echo "sudo pacman -S --needed nvidia-utils lib32-nvidia-utils";; fedora) echo "install NVIDIA's driver from RPM Fusion: sudo dnf install akmod-nvidia xorg-x11-drv-nvidia-libs.i686";; debian) echo "install NVIDIA's proprietary driver (Ubuntu: sudo ubuntu-drivers install)";; *) echo "install NVIDIA's proprietary driver for your distro";; esac) ;;
    amd)    pat='radeon' pkg=$(pkg_cmd 'vulkan-radeon lib32-vulkan-radeon' 'mesa-vulkan-drivers mesa-vulkan-drivers.i686' mesa-vulkan-drivers libvulkan_radeon) ;;
    intel)  pat='intel'  pkg=$(pkg_cmd 'vulkan-intel lib32-vulkan-intel' 'mesa-vulkan-drivers mesa-vulkan-drivers.i686' mesa-vulkan-drivers libvulkan_intel) ;;
  esac
  if printf '%s' "$ICDS" | grep -q "$pat"; then ok "Vulkan driver for $gpu GPU installed"
  else bad "No Vulkan driver for your $gpu GPU"; fix "$pkg"; fi
done
[ -z "$GPUS" ] && info "Couldn't identify the GPU — skipped the Vulkan driver check"
# AMD's AMDVLK driver next to Mesa's RADV: Proton/DXVK may pick AMDVLK, which breaks many games
if printf '%s' "$ICDS" | grep -q 'amd_icd'; then
  warn "AMDVLK is installed alongside Mesa's RADV driver — Proton can pick AMDVLK, which often fails to start or draw games"
  fix "$(pkg_cmd 'amdvlk lib32-amdvlk' amdvlk amdvlk amdvlk | sed 's/ -S --needed / -R /; s/ install / remove /')   (removes AMDVLK so games use RADV)"
fi
if have vulkaninfo; then
  devs=$(timeout 20 vulkaninfo --summary 2>/dev/null | sed -n 's/^\s*deviceName\s*=\s*//p' | grep -v -i llvmpipe | paste -sd, | sed 's/,/, /g')
  if [ -n "$devs" ]; then ok "Vulkan sees: $devs"
  else warn "vulkaninfo found no hardware GPU (only software rendering) — the game will be unplayably slow or won't start"; fi
fi

# Hybrid-graphics laptops (Optimus): without PRIME offload the game can start on the
# integrated GPU or hang before drawing anything ("Game is running", no window).
HYBRID=0 HYBRID_MESA=0
GPU_COUNT=$(cat /sys/class/drm/card*/device/vendor 2>/dev/null | grep -c .)
if ls /sys/class/power_supply/BAT* >/dev/null 2>&1; then
  case " $GPUS " in
    *" nvidia "*) case " $GPUS " in *" intel "*|*" amd "*) HYBRID=1 ;; esac ;;
    *) [ "$GPU_COUNT" -ge 2 ] && HYBRID_MESA=1 ;;
  esac
fi
is_offload_wrapper() { grep -qs '__NV_PRIME_RENDER_OFFLOAD' "$1"; }
is_dri_prime_wrapper() { grep -qs 'DRI_PRIME=1' "$1"; }
if [ $HYBRID_MESA = 1 ]; then
  if [ -n "$UMU" ] && is_dri_prime_wrapper "$UMU"; then
    ok "Laptop with two GPUs — the umu-run wrapper sends the game to the discrete GPU (DRI_PRIME=1)"
  else
    pair=$(printf '%s\n' $GPUS | paste -sd/ | tr a-z A-Z); [ "$pair" = AMD ] && pair="AMD + AMD"
    warn "Laptop with two GPUs ($pair) — the game may run on the slower integrated GPU"
    fix "$SELF --fix   (installs the umu-run wrapper, which runs the game on the discrete GPU on laptops)"
  fi
fi
if [ $HYBRID = 1 ]; then
  prime=""; have prime-select && prime=$(prime-select query 2>/dev/null)
  if [ ! -e /proc/driver/nvidia/version ]; then
    bad "Laptop with an NVIDIA GPU, but the NVIDIA driver isn't loaded — the game would run on the integrated GPU"
    fix "Install/enable NVIDIA's proprietary driver (Linux Mint/Ubuntu: Driver Manager, or: sudo ubuntu-drivers install), then reboot"
  elif [ "$prime" = intel ]; then
    bad "NVIDIA GPU is switched off (prime-select is set to \"intel\")"
    fix "sudo prime-select on-demand   (then reboot; or pick \"NVIDIA On-Demand\" in the NVIDIA settings app)"
  elif [ -n "$UMU" ] && is_offload_wrapper "$UMU"; then
    ok "Hybrid-graphics laptop — the umu-run wrapper sends the game to the NVIDIA GPU"
  else
    other=$(printf '%s\n' $GPUS | grep -v nvidia | paste -sd/)
    warn "Hybrid-graphics laptop (NVIDIA + ${other^^}) — the game must be told to use the NVIDIA GPU, or it can sit on \"Game is running\" with no window"
    fix "$SELF --fix   (installs the umu-run wrapper, which runs the game on the NVIDIA GPU on hybrid laptops)"
  fi
fi

# ── 6. Wine prefix ────────────────────────────────────────────────────────────
section "6. Game prefix"
PLAYER_LOG="$PREFIX/$LOG_DIR_GAME/Player.log"
if [ -d "$PREFIX/drive_c" ] || [ -d "$PREFIX/pfx/drive_c" ]; then
  ok "Prefix exists ($(tilde "$PREFIX"))"
  [ -f "$PLAYER_LOG" ] && info "Last game log: $(date -r "$PLAYER_LOG" '+%Y-%m-%d %H:%M')  ($(tilde "$PLAYER_LOG"))"
else
  info "Prefix not created yet ($(tilde "$PREFIX")) — umu-run creates it on the first Play"
fi

# ── 7. What the launcher itself logged ────────────────────────────────────────
# The launcher only logs to stdout. The --fix launch script saves that to
# ~/.local/share/mnm/launcher.log; look at the latest session there.
section "7. Launcher log"
LLOG=$MNM_HOME/launcher.log
if [ -f "$LLOG" ]; then
  last=$(awk '/App data directory:/{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}' "$LLOG")
  found=0
  if printf '%s' "$last" | grep -q 'umu-launcher not found'; then
    found=1; bad "Launcher reported \"umu-launcher not found\" on its last run"; fix "$UMU_INSTALL"
  fi
  if printf '%s' "$last" | grep -qE "No module named 'encodings'|Fatal Python error"; then
    found=1; bad "umu-run crashed inside the launcher (AppImage leaked its Python/GTK environment)"
    fix "$SELF --fix   (installs a wrapper that strips the AppImage's environment before umu-run)"
  fi
  if printf '%s' "$last" | grep -q 'Failed to start game via umu-run'; then
    found=1; bad "Launcher: $(printf '%s' "$last" | grep -m1 'Failed to start game via umu-run' | cut -c1-160)"
  fi
  if printf '%s' "$last" | grep -q 'Failed to resolve launcher install directory'; then
    found=1; bad "Launcher couldn't work out its install folder"; fix "Start the launcher from the .appimage file itself (not an extracted copy)"
  fi
  # What umu-run/Proton/Wine printed after the launcher handed off the game
  proton_out=$(printf '%s\n' "$last" | sed -n '/Launching via umu-run/,$p' | scrub \
    | grep -vE "^ProtonFixes|^INFO: |atk-bridge|unable to use parent for game drive|Executable is a unix path|^Proton: /|ntsync: up|^\s*$" | tail -15)
  if printf '%s' "$proton_out" | grep -qiE 'err:|error|fail|vulkan|dxvk|vkd3d|exception|crash|segfault|abort'; then
    warn "Proton/Wine reported problems on the last game start ($(date -r "$LLOG" '+%Y-%m-%d %H:%M')):"
    printf '%s\n' "$proton_out" | detail
  fi
  if [ $found = 0 ]; then
    if printf '%s' "$last" | grep -q 'Proton: .*mnm\.exe'; then ok "Last run handed mnm.exe to Proton cleanly ($(date -r "$LLOG" '+%Y-%m-%d %H:%M'))"
    else ok "No launcher errors in the last session"; fi
  fi
else
  info "No launcher log yet (the launcher only logs when started via the --fix launch script)"
fi

# ── 8. Python (umu-run is a Python program; the check window needs PyGObject + GTK) ──
section "8. Python"
if have python3; then
  ok "Python $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null)"
  if python3 - <<'PY' 2>/dev/null
import gi
for v in ("4.0", "3.0"):
    try:
        gi.require_version("Gtk", v)
        from gi.repository import Gtk
        break
    except (ValueError, ImportError):
        continue
else:
    raise SystemExit(1)
PY
  then ok "PyGObject + GTK installed (the check window can open)"
  else
    warn "PyGObject/GTK not found — the check window (mnm-linux-check.py) falls back to the terminal"
    fix "$(pkg_cmd 'python-gobject gtk4' 'python3-gobject gtk4' 'python3-gi gir1.2-gtk-4.0' 'python3-gobject-Gdk typelib-1_0-Gtk-4_0')   (only needed for the check window)"
  fi
else
  bad "python3 not found — umu-run (and this check's window) need it"
  fix "$(pkg_cmd python python3 python3 python3)"
fi

# ── --fix: env-cleaning wrapper + launch script + menu entry ──────────────────
# The AppImage exports APPDIR/GTK_*/PYTHON* etc. into everything it spawns, including
# umu-run (Python) and Proton. Some setups crash with "No module named 'encodings'".
# A tiny umu-run wrapper first on the launcher's PATH scrubs that environment.
if [ $DO_FIX = 1 ]; then
  section "Applying --fix"
  if [ -z "$APPIMAGE" ]; then
    bad "Can't fix without the launcher AppImage — pass --appimage /path/to/MonstersAndMemories_*.appimage"
  else
    mkdir -p "$WRAP_DIR"
    cat > "$WRAP_DIR/umu-run" <<'EOF'
#!/usr/bin/env bash
# Wrapper used by the Monsters & Memories launcher (AppImage), written by mnm-linux-check.sh.
# The AppImage leaks its bundled Python/GTK/library env into child processes,
# which crashes umu-run ("No module named 'encodings'") and can break Proton.
# Strip those, then hand off to the real umu-run (next one on PATH after this dir).
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH LD_PRELOAD \
      GTK_DATA_PREFIX GTK_THEME GTK_EXE_PREFIX GTK_PATH GTK_IM_MODULE_FILE \
      GDK_PIXBUF_MODULE_FILE GDK_BACKEND GIO_EXTRA_MODULES GSETTINGS_SCHEMA_DIR
if [ -n "$APPDIR" ]; then
  PATH=$(printf '%s' "$PATH" | tr ':' '\n' | grep -v "^$APPDIR" | paste -sd:)
  XDG_DATA_DIRS=$(printf '%s' "$XDG_DATA_DIRS" | tr ':' '\n' | grep -v "^$APPDIR" | paste -sd:)
  export PATH XDG_DATA_DIRS
  unset APPDIR APPIMAGE ARGV0 OWD
fi
# Laptops with two GPUs: run the game on the discrete one (set MNM_NO_PRIME_OFFLOAD=1 to skip).
# NVIDIA Optimus needs PRIME render offload; AMD/Intel + AMD (Mesa) uses DRI_PRIME=1.
if [ -z "${MNM_NO_PRIME_OFFLOAD:-}" ] && ls /sys/class/power_supply/BAT* >/dev/null 2>&1; then
  vendors=$(cat /sys/class/drm/card*/device/vendor 2>/dev/null)
  if [ -e /proc/driver/nvidia/version ] && printf '%s\n' "$vendors" | grep -qvx 0x10de; then
    export __NV_PRIME_RENDER_OFFLOAD=1 __VK_LAYER_NV_optimus=NVIDIA_only __GLX_VENDOR_LIBRARY_NAME=nvidia
  elif [ "$(printf '%s\n' "$vendors" | grep -c .)" -ge 2 ] && [ -z "${DRI_PRIME:-}" ]; then
    export DRI_PRIME=1
  fi
fi
self=$(dirname "$(readlink -f "$0")")
IFS=: read -ra dirs <<< "$PATH"
for d in "${dirs[@]}" /usr/bin /usr/local/bin "$HOME/.local/bin"; do
  [ "$(readlink -f "$d")" = "$self" ] && continue
  [ -x "$d/umu-run" ] && exec "$d/umu-run" "$@"
done
echo "umu-run wrapper: no real umu-run found on PATH" >&2; exit 127
EOF
    chmod +x "$WRAP_DIR/umu-run"
    ok "Wrote $(tilde "$WRAP_DIR/umu-run")"

    LAUNCH=$MNM_HOME/mnm-launcher.sh
    cat > "$LAUNCH" <<EOF
#!/usr/bin/env bash
# Starts the Monsters & Memories launcher with the umu-run wrapper first in PATH,
# saving its output to launcher.log (written by mnm-linux-check.sh).
APP=\$(find "$(dirname "$APPIMAGE")" -maxdepth 1 -iname 'MonstersAndMemories*.appimage' -printf '%T@ %p\\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
export PATH="$WRAP_DIR:\$PATH"
LOG="$MNM_HOME/launcher.log"
[ -f "\$LOG" ] && [ "\$(stat -c%s "\$LOG")" -gt 5000000 ] && mv -f "\$LOG" "\$LOG.old"
exec "\$APP" "\$@" >> "\$LOG" 2>&1
EOF
    chmod +x "$LAUNCH"
    ok "Wrote $(tilde "$LAUNCH")"

    # Point the app-menu entry at the launch script (backing up the original once)
    if [ -n "$DESKTOP_FILE" ]; then
      [ -f "$MNM_HOME/desktop-entry.backup" ] || cp "$DESKTOP_FILE" "$MNM_HOME/desktop-entry.backup"
      sed -i "s|^Exec=.*|Exec=$LAUNCH|" "$DESKTOP_FILE"
      ok "Menu entry $(tilde "$DESKTOP_FILE") now runs the launch script (original saved to $(tilde "$MNM_HOME/desktop-entry.backup"))"
    else
      mkdir -p "$DATA_HOME/applications"
      printf '[Desktop Entry]\nType=Application\nName=Monsters & Memories\nComment=Launcher for Monsters & Memories\nExec=%s\nStartupWMClass=mnm_launcher\nTerminal=false\nCategories=Game;\n' "$LAUNCH" \
        > "$DATA_HOME/applications/mnm-launcher.desktop"
      ok "Created menu entry \"Monsters & Memories\" ($(tilde "$DATA_HOME/applications/mnm-launcher.desktop"))"
    fi
    have update-desktop-database && update-desktop-database "$DATA_HOME/applications" 2>/dev/null
    info "Close the launcher if it's open, then start it from the app menu so it picks this up"
  fi
fi

# ── --test: prove umu → Proton → prefix works, without starting the game ──────
LINK_OK=0
if [ $DO_TEST = 1 ]; then
  section "Test: umu-run → Proton → game prefix"
  if [ $FAILS -gt 0 ] && [ -z "$UMU" ]; then
    bad "Skipped — install umu-run first"
  elif pgrep -u "$(id -u)" -f 'mnm\.exe' >/dev/null; then
    # Don't poke the prefix while someone's playing — and the game being up already
    # proves the link; --watch confirms it without touching anything.
    info "Skipped — the game is running. Close it first, or use --watch instead"
  else
    U=${UMU:-umu-run}
    tlog=$(mktemp)
    info "Running 'cmd /c echo' in $(tilde "$PREFIX") with the launcher's exact settings…"
    info "(first run may download GE-Proton + runtime — can take a few minutes)"
    # Same env the launcher passes; scrub the AppImage-ish vars in case this runs from one.
    # PROTON_VERB=run: the default waitforexitandrun blocks until the whole prefix
    # (wineserver) goes idle, which can hang long after cmd itself has exited.
    env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONHOME -u PYTHONPATH \
        WINEPREFIX="$PREFIX" GAMEID=$GAMEID PROTONPATH="$PROTON" PROTON_VERB=run \
        timeout 900 "$U" cmd /c echo MNM_LINK_OK < /dev/null > "$tlog" 2>&1
    if grep -q MNM_LINK_OK "$tlog"; then
      ok "umu-run started Proton in the game prefix and ran a Windows command"
      LINK_OK=1
    else
      bad "umu-run couldn't run a Windows program in the game prefix. Last output:"
      grep -v -E 'ProtonFixes|^\s*$' "$tlog" | tail -8 | sed 's/^/        /'
      fix "Check the output above; common causes are no network on first run (GE-Proton download) or a missing Vulkan driver"
    fi
    rm -f "$tlog"
  fi
fi

# ── --watch: confirm the real thing — launcher → umu-run → mnm.exe ────────────
# Looks for a process whose argv[0] is mnm.exe carrying the launcher's GAMEID, then
# waits for the game to write Player.log in the prefix. Never prints its arguments.
find_game() {
  local p a0
  for p in $(pgrep -u "$(id -u)" -f 'mnm\.exe' 2>/dev/null); do
    a0=$(tr '\0' '\n' < "/proc/$p/cmdline" 2>/dev/null | head -1)
    case ${a0,,} in *mnm.exe) [ "$(envof "$p" GAMEID)" = "$GAMEID" ] && { echo "$p"; return; } ;; esac
  done
}
started_at() {  # epoch seconds a process started (the /proc/<pid> mtime is only when it was first looked up)
  local ticks btime
  ticks=$(awk '{print $22}' "/proc/$1/stat" 2>/dev/null) btime=$(awk '/^btime/{print $2}' /proc/stat)
  echo $(( btime + ${ticks:-0} / $(getconf CLK_TCK) ))
}
gpu_name() {  # gpu_name <drm card/render node name> → "AMD (integrated)", "NVIDIA", …
  local dev=/sys/class/drm/$1/device v n
  v=$(cat "$dev/vendor" 2>/dev/null)
  case $v in 0x10de) n=NVIDIA ;; 0x1002) n=AMD ;; 0x8086) n=Intel ;; *) n="GPU $v" ;; esac
  if [ "${HYBRID_MESA:-0}" = 1 ] && [ "$v" != 0x10de ]; then   # integrated vs discrete only matters on AMD/Intel laptops
    [ "$(cat "$dev/boot_vga" 2>/dev/null)" = 1 ] && n="$n (integrated)" || n="$n (discrete)"
  fi
  echo "$n"
}
game_gpus() {  # GPUs the game process has open, e.g. "NVIDIA" or "AMD (integrated)"
  local f t
  for f in /proc/"$1"/fd/*; do
    t=$(readlink "$f" 2>/dev/null) || continue
    case $t in
      /dev/nvidia[0-9]*) echo NVIDIA ;;
      /dev/dri/renderD*|/dev/dri/card*) gpu_name "${t#/dev/dri/}" ;;
    esac
  done | sort -u | paste -sd, | sed 's/,/, /g'
}
if [ $DO_WATCH = 1 ]; then
  section "Watch: launcher → mnm.exe"
  start=$(date +%s); GPID=$(find_game); saw_umu=0
  if [ -n "$GPID" ]; then info "Game is already running"
  else
    info "Open the launcher and press Play — waiting up to 10 minutes (Ctrl+C to stop)…"
    while [ -z "$GPID" ] && [ $(( $(date +%s) - start )) -lt 600 ]; do
      sleep 2; GPID=$(find_game)
      if pgrep -u "$(id -u)" -f 'umu-run .*mnm\.exe' >/dev/null; then
        [ $saw_umu = 0 ] && info "Launcher called umu-run — starting Proton…"; saw_umu=1
      elif [ $saw_umu = 1 ] && [ -z "$GPID" ]; then
        bad "umu-run exited before mnm.exe appeared — Proton failed to start the game"
        fix "$SELF --test   (shows Proton's error) and check $(tilde "$MNM_HOME/launcher.log")"
        break
      fi
    done
  fi
  if [ -n "$GPID" ]; then
    a0=$(tr '\0' '\n' < "/proc/$GPID/cmdline" | head -1 | scrub)
    ok "mnm.exe is running (pid $GPID, $a0)"
    wp=$(envof "$GPID" STEAM_COMPAT_DATA_PATH); wp=${wp:-$(envof "$GPID" WINEPREFIX)}
    [ "${wp%/}" = "${PREFIX%/}" ] && ok "Started by umu-run in the launcher's prefix" \
      || warn "Running from a different prefix ($(tilde "$wp")) — the map app looks in $(tilde "$PREFIX")"
    pp=$(envof "$GPID" PROTONPATH); [ -n "$pp" ] && ok "Proton: $(basename "$pp")"
    # Did the GPU-offload settings from the wrapper reach the game?
    offload=""
    [ "$(envof "$GPID" __NV_PRIME_RENDER_OFFLOAD)" = 1 ] && offload="NVIDIA PRIME offload"
    [ "$(envof "$GPID" DRI_PRIME)" = 1 ] && offload="DRI_PRIME=1"
    if [ -n "$offload" ]; then ok "Game got the GPU-offload settings ($offload)"
    elif [ $HYBRID = 1 ] || [ $HYBRID_MESA = 1 ]; then
      bad "Game started WITHOUT the GPU-offload settings — the launcher didn't go through the fix's umu-run wrapper"
      fix "Close the launcher and start it from the \"Monsters & Memories\" app-menu entry that Apply launcher fix created (not by double-clicking the AppImage). If you never applied it: $SELF --fix"
    fi
    info "Waiting for the game to write its log…"
    gstart=$(started_at "$GPID") gpus=""
    for _ in $(seq 1 45); do
      g=$(game_gpus "$GPID"); [ -n "$g" ] && gpus=$g
      [ -f "$PLAYER_LOG" ] && [ "$(stat -c %Y "$PLAYER_LOG")" -ge "$gstart" ] && break
      sleep 2
    done
    g=$(game_gpus "$GPID"); [ -n "$g" ] && gpus=$g
    if [ -f "$PLAYER_LOG" ] && [ "$(stat -c %Y "$PLAYER_LOG")" -ge "$gstart" ]; then
      ok "Game is writing Player.log — the map app can follow your zones"
      LINK_OK=1
      # Unity logs the GPU it renders on; Vulkan opens every GPU, so open devices don't tell
      sleep 3
      renderer=$(grep -m1 -E '^\s*Renderer:' "$PLAYER_LOG" | sed -E 's/^\s*Renderer:\s*//; s/ \(ID=[^)]*\)//')
      if [ -z "$renderer" ]; then info "Game hasn't logged its GPU yet"
      elif [ $HYBRID = 1 ] && ! printf '%s' "$renderer" | grep -qi nvidia; then
        bad "Game is rendering on $renderer, not the NVIDIA GPU"
        fix "$SELF --fix   (then start the launcher from its app-menu entry so the game runs on NVIDIA)"
      else ok "Game is rendering on: $renderer"; fi
    else
      bad "mnm.exe is running but hasn't written Player.log after 90 seconds ($(tilde "$PLAYER_LOG")) — the game is stuck before its first screen"
      if [ -n "$gpus" ]; then info "GPUs the game has opened so far: $gpus"
      else info "The game hasn't opened any GPU — it's stuck before starting its renderer"; fi
      if [ -f "$LLOG" ]; then
        tail_out=$(awk '/App data directory:/{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}' "$LLOG" | sed -n '/Launching via umu-run/,$p' \
          | scrub | grep -vE "^ProtonFixes|^INFO: |atk-bridge|^\s*$" | tail -15)
        [ -n "$tail_out" ] && { info "Proton/Wine output for this start (from $(tilde "$LLOG")):"; printf '%s\n' "$tail_out" | detail; }
      fi
      fix "Press “Copy report” (or copy this output) and post it at https://github.com/cooldead/mnm-linux-check/issues"
      [ $HYBRID = 1 ] && ! is_offload_wrapper "${UMU:-/nonexistent}" && fix "$SELF --fix   (on hybrid laptops the game can hang like this on the integrated GPU; the wrapper moves it to NVIDIA)"
    fi
  elif [ $saw_umu = 0 ]; then
    bad "No mnm.exe appeared within 10 minutes"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
if [ "$GUI" = 1 ]; then
  for f in "${FIXES[@]}"; do printf '@@fix\t%s\n' "$f"; done
  if [ $FAILS -eq 0 ] && [ $LINK_OK = 1 ]; then printf '@@result\tconfirmed\n'; exit 0
  elif [ $FAILS -eq 0 ]; then printf '@@result\tready\n'; exit 0
  else printf '@@result\tproblems\t%d\n' "$FAILS"; exit 1; fi
fi
section "Result"
if [ ${#FIXES[@]} -gt 0 ]; then
  printf '  %sDo these, in order, then run this check again:%s\n' "$B" "$N"
  i=1; for f in "${FIXES[@]}"; do printf '   %d. %s\n' $i "$f"; i=$((i+1)); done
fi
if [ $FAILS -eq 0 ] && [ $LINK_OK = 1 ]; then
  printf '  %s✓ Launcher ↔ mnm.exe link confirmed.%s\n' "$G" "$N"; exit 0
elif [ $FAILS -eq 0 ]; then
  printf '  %s✓ Everything the launcher needs is in place.%s\n' "$G" "$N"
  printf '  To confirm the link: run with %s--watch%s and press Play (or %s--test%s to check Proton without starting the game).\n' "$B" "$N" "$B" "$N"
  exit 0
else
  printf '  %s✗ %d problem(s) found — the launcher can'"'"'t start mnm.exe yet.%s\n' "$R" "$FAILS" "$N"; exit 1
fi
