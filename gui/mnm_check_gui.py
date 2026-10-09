#!/usr/bin/env python3
"""MnM on Linux: set up, play and troubleshoot Monsters & Memories on Linux.

Sets up the official Linux launcher (download, umu-run, launcher fix), starts it with your
graphics settings, and runs the launcher check when something goes wrong. Needs Python 3 +
PyGObject (GTK 4 or 3); without them, or with --cli, it runs the check in the terminal.
Unofficial community tool, not affiliated with or endorsed by the Monsters & Memories team.

    python3 mnm-on-linux.py               open the window
    python3 mnm-on-linux.py --cli ...     terminal check (same options as the .sh: --test, --watch, --fix, --appimage PATH)
    add --report to print it without personal data (home paths, user/host names, tokens), for bug reports

MNM_CHECK_EVENTLOG=FILE appends a timestamped log of everything done in the window
(buttons, dialogs, chosen files, check output) to FILE, with personal data removed.
"""
import getpass
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

# build.sh replaces this with the full mnm-linux-check.sh, making this file self-contained.
CHECK_SCRIPT = r'''@@CHECK_SCRIPT@@'''

APP_NAME = "MnM on Linux"
APP_VERSION = "2.1.2"
TITLE = APP_NAME
APP_ID = "io.github.mnm.LinuxCheck"
REPO = "cooldead/mnm-linux-check"
APP_ASSET = "mnm-on-linux.py"
LAUNCHER_URL = ("https://pub-f06cad9ebbcd412bb0f4ff64f0f6a3d7.r2.dev/launcher_v2/installer/"
                "MonstersAndMemories_amd64.AppImage")
UMU_REPO = "Open-Wine-Components/umu-launcher"
LINKS = {
    "github": f"https://github.com/{REPO}",
    "issues": f"https://github.com/{REPO}/issues/new/choose",
    "site": "https://monstersandmemories.com",
    "discord": "https://discord.gg/monstersandmemories",
}

HOME = os.path.expanduser("~")
DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
MNM_HOME = os.path.join(DATA_HOME, "mnm")
LAUNCH_SCRIPT = os.path.join(MNM_HOME, "mnm-launcher.sh")
SETTINGS_FILE = os.path.join(CONFIG_HOME, "mnm-on-linux", "settings.conf")
GAME_HOME = os.path.join(HOME, "Games", "MonstersAndMemories")
UMU_HOME = os.path.join(HOME, ".local", "bin", "umu-run")


def check_script():
    if not CHECK_SCRIPT.startswith("@@"):
        return CHECK_SCRIPT
    # Unbuilt dev copy: use the script next to the project root.
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "mnm-linux-check.sh"), encoding="utf-8") as f:
        return f.read()


def write_script():
    fd, path = tempfile.mkstemp(prefix="mnm-linux-check-", suffix=".sh")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(check_script())
    return path


def self_command():
    return "python3 " + shlex.quote(os.path.abspath(sys.argv[0]))


def check_env(gui):
    env = dict(os.environ, MNM_CHECK_SELF=self_command() + ("" if gui else " --cli"))
    if gui:
        env["MNM_CHECK_GUI"] = "1"
    return env


def gtk_install_hint():
    ids = ""
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith(("ID=", "ID_LIKE=")):
                    ids += " " + line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    ids = f" {ids} "
    if any(f" {d} " in ids for d in ("arch", "cachyos", "manjaro", "endeavouros")):
        return "sudo pacman -S --needed python-gobject gtk4"
    if any(f" {d} " in ids for d in ("fedora", "rhel", "nobara", "bazzite")):
        return "sudo dnf install python3-gobject gtk4"
    if any(f" {d} " in ids for d in ("debian", "ubuntu", "pop", "linuxmint")):
        return "sudo apt install python3-gi gir1.2-gtk-4.0"
    if "suse" in ids:
        return "sudo zypper install python3-gobject-Gdk typelib-1_0-Gtk-4_0"
    return "install PyGObject and GTK 4 (or GTK 3) with your package manager"


def run_cli(args):
    path = write_script()
    report = "--report" in args
    args = [a for a in args if a != "--report"]
    try:
        if report:
            # Same check, printed with personal data removed, for pasting into bug reports
            proc = subprocess.run(["bash", path, *args], env=check_env(False), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            print(scrub(proc.stdout), end="")
            return proc.returncode
        return subprocess.call(["bash", path, *args], env=check_env(False))
    except KeyboardInterrupt:
        return 130
    finally:
        os.unlink(path)


def no_gui_fallback(args, reason):
    hint = gtk_install_hint()
    message = (f"The check window needs PyGObject + GTK ({reason}).\n"
               f"To get the window, install it with:\n  {hint}\n\n"
               f"Running the check in the terminal instead.")
    if sys.stdout.isatty():
        print(message + "\n")
        return run_cli(args)
    # Double-clicked with no terminal to print to: say so in a desktop dialog if possible.
    text = message.replace("Running the check in the terminal instead.",
                           f"Or run it in a terminal:\n  {self_command()} --cli")
    for tool in (["kdialog", "--sorry", text], ["zenity", "--warning", "--no-wrap", "--text", text],
                 ["notify-send", TITLE, text]):
        if shutil.which(tool[0]):
            subprocess.call(tool)
            break
    return 1


# ── personal data never goes into a copied report ───────────────────────────────
# Keep in step with scrub() in mnm-linux-check.sh.
COMMON_NAMES = {"amd", "deck", "game", "games", "gamer", "intel", "linux", "mnm", "nvidia", "root", "steam", "user"}
OCTET = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
SCRUB_RULES = [
    (r"(?:/var)?/home/[^/\s]+", "~"),
    (r"/(run/media|media)/[^/\s]+", r"/\1/<user>"),
    (r"(?i)[A-Za-z]:\\(?:users|home)\\[^\\\s]+", "~"),
    (r"(--token[= ])\S+", r"\1<hidden>"),
    (r"(?i)\b(token|password|secret|auth)=[^\s&]+", r"\1=<hidden>"),
    (r"[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}", "<email>"),
    (r"eyJ[\w-]{8,}\.[\w.-]+", "<hidden>"),
    (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<id>"),
    (r"(?<![0-9a-fA-F])[0-9a-fA-F]{24,}(?![0-9a-fA-F])", "<id>"),
    (r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", "<mac>"),
    (rf"(?<![\d.])(?:{OCTET}\.){{3}}{OCTET}(?![\d.])", "<ip>"),
]


def scrub(text):
    """Remove personal data from report text: home folders, user and computer name,
    login/web tokens, e-mail, IP/MAC addresses and IDs."""
    for pattern, repl in SCRUB_RULES:
        text = re.sub(pattern, repl, text)
    home = os.path.expanduser("~")   # home folders outside /home (after the rules, so /var/home stays whole)
    if home not in ("", "/"):
        text = text.replace(home, "~")
    for name in (getpass.getuser(), socket.gethostname()):
        if name and len(name) >= 3 and name.lower() not in COMMON_NAMES:
            text = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", "<name>", text)
    return text


def event_log(what, detail=""):
    """Testing aid: record window activity when MNM_CHECK_EVENTLOG is set."""
    path = os.environ.get("MNM_CHECK_EVENTLOG")
    if not path:
        return
    stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(scrub(f"{stamp}  {what:<14} {detail}".rstrip()) + "\n")
    except OSError:
        pass


# ── settings (read by the umu-run wrapper on every Play) ───────────────────────
SETTINGS_CHOICES = {
    "MNM_GPU": ("auto", "discrete", "default"),
    "MNM_RENDERER": ("dxvk", "wined3d"),
    "MNM_WAYLAND": ("0", "1"),
    "MNM_HDR": ("0", "1"),
    "MNM_MANGOHUD": ("0", "1"),
    "MNM_GAMEMODE": ("0", "1"),
}


ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse_launch_options(text):
    """Steam-style launch options: "VAR=value … %command% -args". Returns (env, pre, args), where pre is a
    command the game runs through (e.g. "taskset -c 0-3") and args go to mnm.exe. Without %command%,
    VAR=value words set variables and everything else goes to the game. Raises ValueError on bad quoting."""
    words = shlex.split(text)
    is_env = lambda w: "=" in w and ENV_NAME.fullmatch(w.split("=", 1)[0]) is not None
    if words.count("%command%") > 1:
        raise ValueError("%command% can only appear once")
    if "%command%" in words:
        at = words.index("%command%")
        before, args = words[:at], words[at + 1:]
        n = next((i for i, w in enumerate(before) if not is_env(w)), len(before))
        env, pre = before[:n], before[n:]
    else:
        env, pre, args = [w for w in words if is_env(w)], [], [w for w in words if not is_env(w)]
    if any("\n" in w or "\0" in w for w in words):
        raise ValueError("line breaks aren't allowed")
    return env, pre, args


def load_settings():
    settings = {key: values[0] for key, values in SETTINGS_CHOICES.items()}
    settings["MNM_LAUNCH_OPTIONS"] = ""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.rstrip("\n").partition("=")
                if value in SETTINGS_CHOICES.get(key, ()):
                    settings[key] = value
                elif key == "MNM_LAUNCH_OPTIONS":
                    settings[key] = value
    except OSError:
        pass
    return settings


def save_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    env, pre, args = parse_launch_options(settings.get("MNM_LAUNCH_OPTIONS", ""))
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# MnM on Linux settings: used by the umu-run wrapper each time the game starts\n")
        for key in SETTINGS_CHOICES:
            f.write(f"{key}={settings[key]}\n")
        # Launch options as typed (shown in the window), then split into one word per line for the
        # wrapper, so it never has to parse quotes: MNM_ENV = variable, MNM_PRE = command the game
        # runs through, MNM_ARG = argument for the game.
        f.write(f"MNM_LAUNCH_OPTIONS={settings.get('MNM_LAUNCH_OPTIONS', '')}\n")
        for kind, words in (("MNM_ENV", env), ("MNM_PRE", pre), ("MNM_ARG", args)):
            for word in words:
                f.write(f"{kind}={word}\n")
    os.replace(tmp, SETTINGS_FILE)


def wrapper_has_launch_options():
    try:
        with open(os.path.join(MNM_HOME, "bin", "umu-run"), encoding="utf-8") as f:
            return any(line.startswith("# mnm-wrapper ") and line.split()[2].isdigit() and int(line.split()[2]) >= 3
                       for line in f)
    except OSError:
        return False


def os_ids():
    ids = ""
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith(("ID=", "ID_LIKE=")):
                    ids += " " + line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return f" {ids} "


def distro_family():
    ids = os_ids()
    for family, names in (("arch", ("arch", "cachyos", "manjaro", "endeavouros")),
                          ("fedora", ("fedora", "rhel", "nobara", "bazzite")),
                          ("debian", ("debian", "ubuntu", "pop", "linuxmint"))):
        if any(f" {n} " in ids for n in names):
            return family
    return "suse" if "suse" in ids else "other"


def install_command(*packages):
    pkgs = " ".join(packages)
    if os.path.exists("/run/ostree-booted"):
        return f"rpm-ostree install {pkgs}, then restart the computer"
    if " steamos " in os_ids():
        return f"{pkgs} can't be installed the usual way on SteamOS (its system is read-only)"
    return {"arch": f"sudo pacman -S --needed {pkgs}", "fedora": f"sudo dnf install {pkgs}",
            "debian": f"sudo apt install {pkgs}", "suse": f"sudo zypper install {pkgs}"}.get(
        distro_family(), f"install {pkgs} with your package manager")


# ── downloads (official launcher, umu-run, app updates) ─────────────────────────
def http_get(url, dest=None, progress=None, timeout=30):
    """Fetch url; with dest, stream to dest (via a .part file) calling progress(done, total)."""
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if dest is None:
            return resp.read()
        total = int(resp.headers.get("Content-Length") or 0)
        part, done = dest + ".part", 0
        with open(part, "wb") as f:
            while chunk := resp.read(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if total and done != total:
            os.unlink(part)
            raise OSError(f"download incomplete ({done} of {total} bytes)")
        os.replace(part, dest)
        return dest


def latest_release(repo):
    return json.loads(http_get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=10))


def version_tuple(version):
    return tuple(int(x) for x in re.findall(r"\d+", version)[:3])


def download_launcher(progress):
    os.makedirs(GAME_HOME, exist_ok=True)
    dest = os.path.join(GAME_HOME, "MonstersAndMemories_amd64.AppImage")
    http_get(LAUNCHER_URL, dest, progress, timeout=60)
    os.chmod(dest, 0o755)
    return dest


def install_umu(progress):
    """Install umu-launcher's single-file zipapp as ~/.local/bin/umu-run (no sudo)."""
    try:
        version = latest_release(UMU_REPO)["tag_name"]
    except (OSError, ValueError, KeyError):
        version = "1.4.4"
    url = f"https://github.com/{UMU_REPO}/releases/download/{version}/umu-launcher-{version}-zipapp.tar"
    with tempfile.TemporaryDirectory() as tmp:
        archive = http_get(url, os.path.join(tmp, "umu.tar"), progress, timeout=60)
        with tarfile.open(archive) as tar:
            member = tar.getmember("umu/umu-run")
            if not member.isfile():
                raise OSError("unexpected umu-launcher download")
            data = tar.extractfile(member).read()
    os.makedirs(os.path.dirname(UMU_HOME), exist_ok=True)
    with open(UMU_HOME + ".tmp", "wb") as f:
        f.write(data)
    os.chmod(UMU_HOME + ".tmp", 0o755)
    os.replace(UMU_HOME + ".tmp", UMU_HOME)
    return version


def self_update(asset_url, progress=None):
    """Replace this file with the downloaded release, after checking it is a complete copy."""
    me = os.path.abspath(sys.argv[0])
    new = http_get(asset_url, me + ".new", progress, timeout=60)
    source = open(new, encoding="utf-8").read()
    compile(source, new, "exec")
    if "APP_VERSION" not in source or "@@CHECK_SCRIPT@@" in source:
        os.unlink(new)
        raise OSError("the downloaded update looks incomplete")
    os.chmod(new, os.stat(me).st_mode & 0o777)
    os.replace(new, me)
    return me


# ── what is running right now ───────────────────────────────────────────────────
def running_state():
    """('game' | 'launcher' | None): reads only process names and the game's GAMEID, never arguments."""
    launcher = False
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/comm") as f:
                comm = f.read().strip()
            if comm == "mnm_launcher":
                launcher = True
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv0 = f.read().split(b"\0", 1)[0].decode(errors="replace").lower()
            if argv0.endswith("mnm.exe"):
                with open(f"/proc/{pid}/environ", "rb") as f:
                    if b"GAMEID=umu-monstersandmemories" in f.read().split(b"\0"):
                        return "game"
        except OSError:
            continue
    return "launcher" if launcher else None


# ── fix text → prose + copyable command ─────────────────────────────────────────
COMMAND_START = re.compile(r"(?:^|:\s+|\(\w[\w .]*:\s+)((?:sudo|pipx|chmod|mkdir|bash|python3|cd|curl|wget|mv)\s.*)")


def split_fix(text):
    """Return (prose, command, note) for one fix line from the check."""
    if text.startswith(self_command()):
        return text, None, None
    m = COMMAND_START.search(text)
    if not m:
        return text, None, None
    prose = text[:m.start(1)].rstrip(" :")
    prose = re.sub(r"\s*\((\w[\w .]*)$", r" — on \1", prose)
    command, *rest = re.split(r"\s{3,}", m.group(1), maxsplit=1)
    note = rest[0].strip() if rest else ""
    if command.count(")") > command.count("("):
        command = command[:command.rindex(")")].rstrip()
    if note.startswith("(") and note.endswith(")"):
        note = note[1:-1]
    if note:
        note = note[0].upper() + note[1:]
    return prose, command.strip(), note


def main():
    args = sys.argv[1:]
    if "--cli" in args or "-h" in args or "--help" in args:
        if "--cli" not in args:
            print(__doc__)
        return run_cli([a for a in args if a != "--cli"])
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return no_gui_fallback(args, "no graphical session found")
    try:
        import gi
        version = os.environ.get("MNM_CHECK_GTK")
        for gtk in ([version] if version else ["4.0", "3.0"]):
            try:
                gi.require_version("Gtk", gtk)
                gi.require_version("Gdk", gtk)
                break
            except ValueError:
                continue
        else:
            raise ValueError("GTK 4 or 3 introspection data not found")
        from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: F401
    except (ImportError, ValueError) as err:
        return no_gui_fallback(args, str(err) or "not installed")
    return run_gui(Gtk, Gdk, Gio, GLib, args)


def run_gui(Gtk, Gdk, Gio, GLib, args):
    GTK4 = Gtk.get_major_version() == 4

    # ── GTK 3/4 differences, kept in one place ────────────────────────────────
    def append(box, widget):
        if GTK4:
            box.append(widget)
        else:
            box.pack_start(widget, False, True, 0)

    def set_child(container, widget):
        if GTK4:
            container.set_child(widget)
        else:
            container.add(widget)

    def clear(box):
        if GTK4:
            while (child := box.get_first_child()) is not None:
                box.remove(child)
        else:
            for child in box.get_children():
                box.remove(child)

    def shown(widget):
        if not GTK4:
            widget.show_all()
        return widget

    def dropdown(options, active, on_change):
        """options: [(value, text)]; on_change(value) runs when the player picks another one."""
        values = [value for value, _ in options]
        if GTK4:
            widget = Gtk.DropDown.new_from_strings([text for _, text in options])
            widget.set_selected(values.index(active) if active in values else 0)
            widget.connect("notify::selected", lambda w, _p: on_change(values[w.get_selected()]))
        else:
            widget = Gtk.ComboBoxText()
            for value, text in options:
                widget.append(value, text)
            widget.set_active_id(active)
            widget.connect("changed", lambda w: on_change(w.get_active_id()))
        return widget

    def css_class(widget, *names):
        for name in names:
            if GTK4:
                widget.add_css_class(name)
            else:
                widget.get_style_context().add_class(name)

    def label(text, *classes, wrap=True, selectable=False, xalign=0.0):
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(xalign)
        if wrap:
            if GTK4:
                lbl.set_wrap(True)
            else:
                lbl.set_line_wrap(True)
            lbl.set_max_width_chars(80)
        lbl.set_selectable(selectable)
        css_class(lbl, *classes)
        return lbl

    def box(vertical=True, spacing=6, *classes):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                    spacing=spacing)
        css_class(b, *classes)
        return b

    def copy_text(text):
        if GTK4:
            Gdk.Display.get_default().get_clipboard().set(text)
        else:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)

    def load_css(css):
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):  # GTK 4.12+
            provider.load_from_string(css)
        else:
            # GTK 3 and GTK 4.0-4.8 take bytes only (PyGObject 3.42 on Ubuntu/Pop!_OS 22.04 has no
            # override to adapt the call); GTK 4.9-4.11 without that override want (text, length).
            try:
                provider.load_from_data(css.encode())
            except TypeError:
                provider.load_from_data(css, -1)
        priority = Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        if GTK4:
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, priority)
        else:
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, priority)

    CSS = """
    .title { font-size: 1.5em; font-weight: bold; }
    .dim { opacity: 0.7; }
    .banner { padding: 12px 14px; border-radius: 8px; }
    .banner-ok { background-color: alpha(#2ec27e, 0.18); }
    .banner-bad { background-color: alpha(#e01b24, 0.16); }
    .banner-busy { background-color: alpha(#3584e4, 0.14); }
    .banner-text { font-weight: bold; font-size: 1.1em; }
    .card { padding: 10px 12px; border-radius: 8px; background-color: alpha(currentColor, 0.05); }
    .step-num { font-weight: bold; font-size: 1.1em; min-width: 1.6em; }
    .cmd { font-family: monospace; padding: 6px 8px; border-radius: 6px; background-color: alpha(currentColor, 0.08); }
    .section { font-weight: bold; margin-top: 8px; }
    .mark { font-weight: bold; min-width: 1.4em; }
    .ok { color: #26a269; }
    .bad { color: #e01b24; }
    .warn { color: #c88800; }
    .detail { font-family: monospace; font-size: 0.9em; opacity: 0.8; }
    .play { font-size: 1.6em; font-weight: bold; padding: 10px 48px; }
    .primary { background-image: none; background-color: #2e7fd8; color: #ffffff; border-color: #2569b4; }
    .primary:hover { background-color: #3a8de8; }
    .primary:disabled { background-color: alpha(#2e7fd8, 0.35); color: alpha(#ffffff, 0.6); }
    """

    MARKS = {"ok": ("✓", "ok"), "bad": ("✗", "bad"), "warn": ("!", "warn"), "info": ("·", "dim")}

    class Window:
        def __init__(self, app):
            self.app = app
            self.proc = None
            self.appimage = None
            self.mode = None
            self.fixes = []
            self.result = None
            self.problem_rows = []
            self.report = []
            self.fix_just_applied = False
            self.last_finished = 0.0
            self.last_mode = None

            self.steps = {}
            self.gpu_info = ("0", "", "")
            self.busy_action = None
            self.skip_update = False
            self.seen_optional = set()
            self.settings = load_settings()

            self.win = Gtk.ApplicationWindow(application=app, title=TITLE)
            self.win.set_default_size(760, 560)
            self.win.connect("close-request" if GTK4 else "delete-event", self.on_close)
            self.win.connect("notify::is-active", self.on_focus)

            outer = box(True, 14)
            for side in ("top", "bottom", "start", "end"):
                getattr(outer, f"set_margin_{side}")(16)
            # Header: name on the left; Settings / Troubleshoot / About (or Back) on the right
            header = box(False, 8)
            titles = box(True, 2)
            titles.set_hexpand(True)
            append(titles, label("MnM on Linux", "title"))
            self.system = label("Set up, play and fix Monsters & Memories on Linux.", "dim")
            append(titles, self.system)
            append(header, titles)
            self.back_btn = Gtk.Button(label="← Back")
            self.back_btn.set_valign(Gtk.Align.START)
            self.back_btn.connect("clicked", lambda *_: self.go("home"))
            append(header, self.back_btn)
            self.nav_btns = []
            for name, text in (("settings", "Settings"), ("troubleshoot", "Troubleshoot"), ("about", "About")):
                btn = Gtk.Button(label=text)
                btn.set_valign(Gtk.Align.START)
                btn.connect("clicked", lambda _b, n=name: self.go(n))
                append(header, btn)
                self.nav_btns.append(btn)
            append(outer, header)

            # One view at a time: the current setup step (or Play), or one of the extra pages
            self.stack = Gtk.Stack()
            self.stack.set_vexpand(True)
            self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            append(outer, self.stack)
            for name, build in (("home", self.build_home), ("settings", self.build_settings),
                                ("troubleshoot", self.build_troubleshoot), ("about", self.build_about)):
                self.stack.add_named(build(), name)

            set_child(self.win, outer)
            shown(self.win)
            self.stop_btn.set_visible(False)
            self.update_btn.set_visible(False)
            self.play_box.set_visible(False)
            self.note.set_visible(False)
            self.go("home")
            self.win.present()
            event_log("window-open", f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}")
            GLib.timeout_add_seconds(2, self.refresh_running)
            threading.Thread(target=self.check_updates, args=(False,), daemon=True).start()
            mode = next((m for m in ("fix", "test", "watch") if f"--{m}" in args), "check")
            if mode != "check":
                self.go("troubleshoot")
            self.run(args, mode)

        def go(self, name):
            self.stack.set_visible_child_name(name)
            self.back_btn.set_visible(name != "home")
            for btn in self.nav_btns:
                btn.set_visible(name == "home")

        def scrolled(self, child):
            scroller = Gtk.ScrolledWindow()
            scroller.set_vexpand(True)
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            child.set_margin_end(6)
            set_child(scroller, child)
            return scroller

        # ── Troubleshoot: the launcher check ─────────────────────────────────
        def build_troubleshoot(self):
            page = box(True, 10)
            self.banner = box(False, 10, "banner", "banner-busy")
            self.spinner = Gtk.Spinner()
            append(self.banner, self.spinner)
            self.banner_text = label("Starting…", "banner-text")
            self.banner_text.set_hexpand(True)
            append(self.banner, self.banner_text)
            self.stop_btn = Gtk.Button(label="Stop")
            self.stop_btn.connect("clicked", lambda *_: self.stop())
            append(self.banner, self.stop_btn)
            append(page, self.banner)

            content = box(True, 12)
            self.todo_title = label("What to do", "section")
            self.todo = box(True, 8)
            append(content, self.todo_title)
            append(content, self.todo)
            self.details_title = label("Details", "section")
            self.details = box(True, 3)
            append(content, self.details_title)
            append(content, self.details)
            append(page, self.scrolled(content))

            actions = box(False, 8)
            self.buttons = {}
            for key, text, tip in (
                ("check", "Check again", "Re-run every check. Changes nothing on your system."),
                ("test", "Test Proton", "Runs a harmless Windows command through umu-run in the game's prefix, "
                                        "without starting the game. The first run can download GE-Proton "
                                        "(about 500 MB) and take a few minutes."),
                ("watch", "Watch for Play", "Waits up to 10 minutes for you to press Play in the launcher, "
                                            "then confirms the game actually started."),
                ("fix", "Apply launcher fix…", "Installs the umu-run wrapper and launch script that stop the "
                                               "AppImage's environment from breaking umu-run. Asks first."),
                ("appimage", "Choose AppImage…", "Point the check at your MonstersAndMemories .appimage "
                                                 "if it isn't in ~/Games, ~/Applications, ~/Downloads or similar."),
                ("report", "Copy report", "Copy the full result as text, to paste into a bug report or Discord. "
                                          "Personal data (login token, user and computer name, home folder path) is left out."),
            ):
                btn = Gtk.Button(label=text)
                btn.set_tooltip_text(tip)
                btn.connect("clicked", self.on_action, key)
                append(actions, btn)
                self.buttons[key] = btn
            append(page, actions)
            append(page, label("Hover a button to see what it does. Nothing is installed or changed unless you "
                               "press “Apply launcher fix…” and confirm.", "dim"))
            return page

        # ── shared helpers ──────────────────────────────────────────────────
        def open_uri(self, uri):
            event_log("open", uri if uri.startswith("http") else "(folder)")
            if uri.startswith("/"):
                uri = "file://" + uri
            try:
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except GLib.Error:
                subprocess.Popen(["xdg-open", uri], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def confirm(self, title, text, ok_label, on_ok):
            dialog = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                       message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.NONE, text=title)
            dialog.set_property("secondary-text", text)
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button(ok_label, Gtk.ResponseType.OK)

            def response(d, r):
                event_log("confirm", f"{title} -> {'OK' if r == Gtk.ResponseType.OK else 'cancel'}")
                d.destroy()
                if r == Gtk.ResponseType.OK:
                    on_ok()
            dialog.connect("response", response)
            dialog.present()

        def link_button(self, text, uri):
            btn = Gtk.Button(label=text)
            btn.connect("clicked", lambda *_: self.open_uri(uri() if callable(uri) else uri))
            return btn

        def ready_to_play(self):
            done = lambda k: self.steps.get(k, ("todo",))[0] in ("done", "update")   # an older fix still works
            return all(done(k) for k in ("launcher", "umu", "fix")) and \
                self.steps.get("fuse", ("done",))[0] == "done" and "prefix" not in self.steps

        # ── Home: the current setup step, one at a time; Play once everything is done ──
        def build_home(self):
            page = box(True, 14)
            self.step_count = label("Checking your setup…", "dim")
            append(page, self.step_count)
            self.note = label("", "ok")   # what the last step did ("✓ umu-run 1.4.4 is installed.")
            append(page, self.note)
            self.step_box = box(True, 12)
            append(page, self.step_box)

            self.play_box = box(True, 14)
            self.play_status = label("", "banner-text")
            append(self.play_box, self.play_status)
            self.play_btn = Gtk.Button(label="Play")
            css_class(self.play_btn, "suggested-action", "primary", "play")
            self.play_btn.set_halign(Gtk.Align.START)
            self.play_btn.connect("clicked", self.on_play)
            append(self.play_box, self.play_btn)
            self.play_hint = label("", "dim")
            append(self.play_box, self.play_hint)
            append(self.play_box, label("Play opens the official launcher with the launcher fix and your Settings. "
                                        "Sign in, install or update the game there, then press its Play button.",
                                        "dim"))
            links = box(False, 8)
            append(links, self.link_button("Game folder", lambda: self.game_folder()))
            append(links, self.link_button("Logs folder", MNM_HOME))
            append(links, self.link_button("M&M website", LINKS["site"]))
            append(links, self.link_button("M&M Discord", LINKS["discord"]))
            append(self.play_box, links)
            append(page, self.play_box)

            spacer = box(True, 0)
            spacer.set_vexpand(True)
            append(page, spacer)
            self.trail = label("", "dim")
            append(page, self.trail)
            return page

        def game_folder(self):
            detail = self.steps.get("game", ("", ""))[1]
            return detail.replace("~", HOME, 1) if detail.startswith("~") else (detail or GAME_HOME)

        def on_play(self, *_):
            event_log("button", "play")
            if not self.ready_to_play():
                self.go("home")
                return
            if running_state():
                self.play_hint.set_text("The launcher or game is already open.")
                return
            subprocess.Popen([LAUNCH_SCRIPT], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.play_hint.set_text("Starting the launcher… (it can take a few seconds to appear)")

        def refresh_running(self):
            state = running_state()
            if state == "game":
                self.play_status.set_text("✓ The game is running.")
            elif state == "launcher":
                self.play_status.set_text("The launcher is open. Press Play in the launcher to start the game.")
            else:
                self.play_status.set_text("✓ Ready to play.")
            self.play_btn.set_sensitive(state is None)
            return True

        def step_rows(self):
            """[(key, short name, title, {state: (text, button, action)})] in the order a new player does them."""
            rows = [
                ("launcher", "Launcher", "Get the official launcher",
                 {"todo": ("Downloads the official Linux launcher (about 107 MB) from the Monsters & Memories "
                           "download page into ~/Games/MonstersAndMemories. The game gets installed next to it.",
                           "Download launcher", self.do_download),
                  "chmod": ("Your launcher file isn't allowed to run yet. This marks it as a program.",
                            "Make it runnable", self.do_chmod)}),
                ("fuse", "FUSE", "Install FUSE 2 (lets the launcher file open)",
                 {"todo": ("This needs your password, so copy this command into a terminal and run it. "
                           "Then come back here: the next step appears by itself.", None, None)}),
                ("userns", "Sandbox", "Allow Proton's sandbox",
                 {"todo": ("Proton runs the game inside a sandbox, and this system doesn't allow normal users to "
                           "create one (user namespaces are turned off). This needs your password, so copy this "
                           "command into a terminal and run it. Then come back here.", None, None)}),
                ("umu", "umu-run", "Install umu-run",
                 {"todo": ("umu-run is the tool the launcher uses to start the game with Proton. This downloads "
                           "its single-file version from its GitHub page into ~/.local/bin. No password needed.",
                           "Install umu-run", self.do_umu)}),
                ("fix", "Launcher fix", "Apply the launcher fix",
                 {"todo": ("Required: the launcher passes its own Python settings to umu-run, which makes it crash "
                           "the moment you press Play. This adds a small umu-run wrapper that removes them and "
                           "uses your Settings, plus an app-menu entry “Monsters & Memories”.",
                           "Apply fix", self.do_fix),
                  "update": ("Your launcher fix is an older version. Update it so your Settings are used and the "
                             "launcher window doesn't stay white on newer distros.",
                             "Update fix", self.do_fix),
                  "needed": ("Required: your launcher fix is an older version, and on this system the launcher "
                             "can't start without the new one (its bundled text library needs a newer HarfBuzz "
                             "than your system has, so it quits at once).",
                             "Update fix", self.do_fix)}),
                ("game", "Game", "Install the game",
                 {"todo": ("Open the launcher, sign in and press Install (about 7 GB). The game goes into "
                           "the folder next to the launcher. Come back here when it's done.",
                           "Open launcher", self.on_play)}),
            ]
            if "prefix" in self.steps:
                rows.insert(0, ("prefix", "Repair", "Repair the game's Wine folder",
                                {"damaged": ("Some Windows files Proton made for the game are empty (usually after a "
                                             "crash or power-off during the first start), so the game can't open. "
                                             "This moves the folder aside; a fresh one is made on the next Play. "
                                             "Your game download isn't touched.", "Repair", self.do_repair)}))
            return rows

        def refresh_setup(self):
            clear(self.step_box)
            self.note.set_visible(bool(self.note.get_text()))
            if not self.steps:
                return
            state_of = lambda k: self.steps.get(k, ("done", ""))[0]
            # FUSE and the sandbox are only needed on some systems: list them once they've come up, so the
            # step count doesn't change halfway through
            self.seen_optional |= {k for k in ("fuse", "userns") if state_of(k) != "done"}
            rows = [r for r in self.step_rows() if r[0] not in ("fuse", "userns") or r[0] in self.seen_optional]
            pending = lambda k: state_of(k) != "done" and not (state_of(k) == "update" and self.skip_update)
            current = next((i for i, (k, *_r) in enumerate(rows) if pending(k)), None)
            self.trail.set_text("   ".join(("✓ " if not pending(k) else "● " if i == current else "○ ") + short
                                           for i, (k, short, *_r) in enumerate(rows)))
            self.play_box.set_visible(current is None)
            if current is None:
                self.step_count.set_text("Setup is complete.")
                self.refresh_running()
                return
            key, _short, title, states = rows[current]
            state = state_of(key)
            self.step_count.set_text(f"Step {current + 1} of {len(rows)}")
            append(self.step_box, shown(label(title, "title")))
            text, button, action = states.get(state, states.get("todo", ("", None, None)))
            append(self.step_box, shown(label(text)))
            if key in ("fuse", "userns"):
                game_cmd = self.steps[key][1]
                row = box(False, 8)
                cmd = label(game_cmd, "cmd", selectable=True)
                cmd.set_hexpand(True)
                append(row, cmd)
                copy = Gtk.Button(label="Copy")
                copy.connect("clicked", self.on_copy, game_cmd)
                append(row, copy)
                append(self.step_box, shown(row))
            buttons = box(False, 8)
            if button:
                btn = Gtk.Button(label=button)
                css_class(btn, "suggested-action", "primary")
                btn.set_sensitive(self.busy_action is None and (key != "game" or self.ready_to_play()))
                btn.connect("clicked", lambda _b, a=action: a())
                append(buttons, btn)
            if key == "launcher" and state == "todo":
                other = Gtk.Button(label="I already have it: choose the file…")
                other.connect("clicked", self.on_action, "appimage")
                append(buttons, other)
            if key == "fix" and state == "update":
                later = Gtk.Button(label="Not now")
                later.connect("clicked", lambda *_: (setattr(self, "skip_update", True), self.refresh_setup()))
                append(buttons, later)
            if key in ("fuse", "userns", "game"):
                again = Gtk.Button(label="Check again")
                again.connect("clicked", lambda *_: self.run([]))
                append(buttons, again)
            append(self.step_box, shown(buttons))
            if self.busy_action == key:
                self.progress = Gtk.ProgressBar()
                self.progress.set_show_text(True)
                append(self.step_box, shown(self.progress))

        def run_task(self, key, work, done_text):
            """Run work(progress) in the background, show progress on the step, then re-check."""
            self.busy_action = key
            self.note.set_text("")
            self.refresh_setup()

            def progress(done, total):
                if total:
                    GLib.idle_add(lambda: (self.progress.set_fraction(done / total),
                                           self.progress.set_text(f"{done >> 20} of {total >> 20} MB"), False)[2])

            def worker():
                try:
                    result = work(progress)
                    message, ok = done_text.format(result=result), True
                except Exception as err:   # report any failure in the window instead of dying silently
                    message, ok = f"That didn't work: {err}", False
                GLib.idle_add(self.task_done, key, message, ok)
            threading.Thread(target=worker, daemon=True).start()

        def task_done(self, key, message, ok):
            event_log("setup-step", f"{key}: {'ok' if ok else 'failed'} | {message}")
            self.busy_action = None
            if ok:
                self.note.set_text("✓ " + message)
            else:
                self.setup_note(message, ok)
            self.run([])
            return False

        def setup_note(self, message, ok=True):
            dialog = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                       message_type=Gtk.MessageType.INFO if ok else Gtk.MessageType.ERROR,
                                       buttons=Gtk.ButtonsType.OK, text=message)
            dialog.connect("response", lambda d, r: d.destroy())
            dialog.present()

        def do_download(self):
            self.confirm("Download the official launcher?",
                         f"Downloads MonstersAndMemories_amd64.AppImage (about 107 MB) from the official "
                         f"Monsters & Memories download page and saves it in {GAME_HOME.replace(HOME, '~')}.",
                         "Download", lambda: self.run_task(
                             "launcher", download_launcher, "The launcher is downloaded."))

        def do_chmod(self):
            path = self.steps["launcher"][1]

            def work(_progress):
                os.chmod(path, os.stat(path).st_mode | 0o111)
            self.confirm("Make the launcher runnable?", "Marks the launcher file as a program so it can start.",
                         "Make runnable", lambda: self.run_task("launcher", work, "The launcher can run now."))

        def do_umu(self):
            self.confirm("Install umu-run?",
                         "Downloads umu-launcher's single-file version (about 0.5 MB) from its GitHub page "
                         "(github.com/Open-Wine-Components/umu-launcher) and saves it as ~/.local/bin/umu-run. "
                         "No password needed.", "Install",
                         lambda: self.run_task("umu", install_umu, "umu-run {result} is installed."))

        def do_fix(self):
            self.confirm_fix()

        def do_repair(self):
            prefix = self.steps["prefix"][1]
            target = prefix + ".broken-" + time.strftime("%Y%m%d-%H%M%S")

            def work(_progress):
                os.rename(prefix, target)
                return target.replace(HOME, "~")
            self.confirm("Repair the game's Wine folder?",
                         f"Moves {prefix.replace(HOME, '~')} to {target.replace(HOME, '~')}. A fresh one is made the "
                         "next time you press Play (the first start then takes a bit longer). Your game download "
                         "isn't touched, and your in-game settings stay in the old folder.", "Repair",
                         lambda: self.run_task("prefix", work, "Done. The old folder is now {result}."))

        # ── Settings ────────────────────────────────────────────────────────
        def build_settings(self):
            page = box(True, 12)
            self.settings_warning = label("", "warn")
            append(page, self.settings_warning)
            self.settings_box = box(True, 12)
            append(page, self.scrolled(self.settings_box))
            append(page, label("Saved right away. Changes apply the next time you press Play. If the game "
                               "acts up after a change, switch it back.", "dim"))
            return page

        def refresh_settings(self):
            clear(self.settings_box)
            fix = self.steps.get("fix", ("todo",))[0]
            self.settings_warning.set_text(
                "" if fix == "done" else "These settings are used once the launcher fix is applied or updated "
                                         "(the setup steps).")
            gpu_count, gpus, laptop = self.gpu_info
            wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"

            def choice(key, title, help_text, options):
                row = box(True, 4)
                append(row, label(title, "section"))
                combo = dropdown(options, self.settings[key], lambda value: self.set_setting(key, value))
                combo.set_halign(Gtk.Align.START)
                append(row, combo)
                append(row, label(help_text, "dim"))
                append(self.settings_box, shown(row))

            def toggle(key, title, help_text, available=True, missing=""):
                row = box(False, 12)
                text = box(True, 2)
                text.set_hexpand(True)
                append(text, label(title, "section"))
                append(text, label(help_text if available else missing, "dim"))
                append(row, text)
                switch = Gtk.Switch()
                switch.set_valign(Gtk.Align.CENTER)
                switch.set_active(self.settings[key] == "1" and available)
                switch.set_sensitive(available)
                switch.connect("notify::active", lambda s, _p: self.set_setting(key, "1" if s.get_active() else "0"))
                append(row, switch)
                append(self.settings_box, shown(row))

            if int(gpu_count or 0) >= 2:
                choice("MNM_GPU", "Graphics card",
                       "Your computer has two graphics cards (" + gpus.strip().replace(" ", " + ").upper() + "). "
                       "The fast one is the separate (discrete) card.",
                       [("auto", "Automatic: use the fast card on laptops"), ("discrete", "Always use the fast card"),
                        ("default", "Let the system decide")])
            else:
                append(self.settings_box, shown(label("Graphics card: there is only one, so there's nothing to "
                                                      "choose. The game uses it automatically.", "dim")))
            choice("MNM_RENDERER", "Graphics translation",
                   "DXVK is fastest. Try WineD3D only if the game shows graphics glitches; it is slower.",
                   [("dxvk", "DXVK (recommended)"), ("wined3d", "WineD3D (compatibility)")])
            toggle("MNM_WAYLAND", "Native Wayland (experimental)",
                   "Runs the game window directly on Wayland instead of through XWayland. Can be smoother; "
                   "turn it off if the window misbehaves.", wayland,
                   "Only for Wayland desktops. Your session isn't Wayland.")
            toggle("MNM_HDR", "HDR", "For HDR monitors on a Wayland desktop with HDR turned on (also turns on "
                   "native Wayland).", wayland, "Needs a Wayland desktop.")
            toggle("MNM_MANGOHUD", "MangoHud overlay", "Shows FPS, frame times and GPU/CPU load in the game.",
                   bool(shutil.which("mangohud")),
                   "MangoHud isn't installed. To install it: " + install_command("mangohud"))
            toggle("MNM_GAMEMODE", "GameMode", "Asks your system to prioritise the game while it runs.",
                   bool(shutil.which("gamemoderun")),
                   "Not needed on Bazzite: it already gives games priority on its own (Bazzite removed "
                   "GameMode on purpose), so there's nothing to turn on." if " bazzite " in os_ids() else
                   "GameMode isn't installed. To install it: " + install_command("gamemode"))
            self.launch_options_row()

        def launch_options_row(self):
            row = box(True, 4)
            append(row, label("Launch options", "section"))
            append(row, label("Like Steam's launch options: VARIABLE=value settings, then %command%, then "
                              "arguments for the game. Examples: %command% -popupwindow (borderless window), "
                              "DXVK_HUD=fps %command% (FPS counter). Leave empty if unsure.",
                              "dim"))
            line = box(False, 6)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_text(self.settings.get("MNM_LAUNCH_OPTIONS", ""))
            entry.set_placeholder_text("e.g. %command% -popupwindow")
            append(line, entry)
            save = Gtk.Button(label="Save")
            append(line, save)
            append(row, line)
            status = label("", "dim", selectable=True)
            append(row, status)

            def describe(text):
                """What the options will do, in plain words, or (None, error)."""
                try:
                    env, pre, args = parse_launch_options(text)
                except ValueError as err:
                    return None, f"Can't use these options: {err}. Check the quotes."
                parts = []
                if env:
                    parts.append("Sets " + ", ".join(env))
                if pre:
                    parts.append("Runs the game through: " + shlex.join(pre))
                if args:
                    parts.append("Passes to the game: " + shlex.join(args))
                return parts, None

            def show(text, saved):
                parts, error = describe(text)
                if error:
                    status.set_text(error)
                    css_class(status, "bad")
                    return
                if GTK4:
                    status.remove_css_class("bad")
                else:
                    status.get_style_context().remove_class("bad")
                lines = parts or ["No launch options."]
                game_args = parse_launch_options(text)[2]
                if "--token" in game_args:
                    lines.append("⚠ --token isn't needed here: the launcher already logs the game in with its own "
                                 "token. A token also works like a password, and it would be saved in plain text. "
                                 "Remove it unless you know you need it.")
                if saved and text.strip() and not wrapper_has_launch_options():
                    lines.append("Launch options need the updated launcher fix: press “Update fix” on the "
                                 "main page (it's listed there now).")
                elif not saved:
                    lines.append("Not saved yet: press Save or Enter.")
                status.set_text("\n".join(lines))

            def on_save(*_):
                text = " ".join(entry.get_text().split("\n")).strip()
                if describe(text)[1]:
                    show(text, False)
                    return
                if text != self.settings.get("MNM_LAUNCH_OPTIONS", ""):
                    self.set_setting("MNM_LAUNCH_OPTIONS", text)
                show(text, self.settings.get("MNM_LAUNCH_OPTIONS") == text)

            entry.connect("activate", on_save)
            save.connect("clicked", on_save)
            entry.connect("changed", lambda e: show(e.get_text(), e.get_text().strip() ==
                                                    self.settings.get("MNM_LAUNCH_OPTIONS", "")))
            show(entry.get_text(), True)
            append(self.settings_box, shown(row))

        def set_setting(self, key, value):
            if value is None or self.settings.get(key) == value:
                return
            previous = self.settings.get(key)
            self.settings[key] = value
            try:
                save_settings(self.settings)
                event_log("setting", f"{key}={value}")
                if key == "MNM_LAUNCH_OPTIONS" and value and not wrapper_has_launch_options():
                    self.skip_update = False   # the old fix ignores launch options: offer "Update fix" again
                    self.run([])
            except (OSError, ValueError) as err:
                self.settings[key] = previous
                self.setup_note(f"Couldn't save settings: {err}", False)

        # ── About ───────────────────────────────────────────────────────────
        def build_about(self):
            page = box(True, 10)
            append(page, label(f"{APP_NAME} {APP_VERSION}", "section"))
            append(page, label("A community tool for playing Monsters & Memories on Linux with the official "
                               "Linux launcher. It is not made, affiliated with or endorsed by the Monsters & "
                               "Memories team. Monsters & Memories and its artwork belong to its developers.", "dim"))
            self.update_text = label("Checking for updates…", "dim")
            append(page, self.update_text)
            row = box(False, 8)
            check = Gtk.Button(label="Check for updates")
            check.connect("clicked", lambda *_: threading.Thread(target=self.check_updates, args=(True,),
                                                                 daemon=True).start())
            append(row, check)
            self.update_btn = Gtk.Button(label="Update")
            css_class(self.update_btn, "suggested-action", "primary")
            self.update_btn.connect("clicked", self.on_update)
            append(row, self.update_btn)
            append(page, row)
            links = box(False, 8)
            append(links, self.link_button("Project page", LINKS["github"]))
            append(links, self.link_button("Report a problem", LINKS["issues"]))
            append(links, self.link_button("M&M website", LINKS["site"]))
            append(links, self.link_button("M&M Discord", LINKS["discord"]))
            append(page, links)
            append(page, label("What it downloads, only when you press the button: the official launcher from the "
                               "Monsters & Memories download page, and umu-launcher (GPL-3.0) from its GitHub page. "
                               "MnM on Linux itself is MIT licensed.", "dim"))
            self.update_asset = None
            return page

        def check_updates(self, manual):
            try:
                release = latest_release(REPO)
                tag = release["tag_name"]
                asset = next((a["browser_download_url"] for a in release.get("assets", [])
                              if a["name"] == APP_ASSET), None)
                newer = version_tuple(tag) > version_tuple(APP_VERSION) and asset
                text = (f"Version {tag.lstrip('v')} is available." if newer
                        else "You have the latest version.")
            except (OSError, ValueError, KeyError) as err:
                asset, newer, text = None, False, ("Couldn't check for updates." if manual else "")
            GLib.idle_add(self.show_update, text, asset if newer else None)

        def show_update(self, text, asset):
            self.update_text.set_text(text)
            self.update_asset = asset
            self.update_btn.set_visible(bool(asset))
            return False

        def on_update(self, *_):
            def work():
                try:
                    path = self_update(self.update_asset)
                    GLib.idle_add(self.update_done, path, None)
                except Exception as err:
                    GLib.idle_add(self.update_done, None, err)
            self.confirm("Update MnM on Linux?",
                         "Downloads the new version from the project's GitHub page and replaces this file. "
                         "Your settings and setup stay as they are.", "Update",
                         lambda: threading.Thread(target=work, daemon=True).start())

        def update_done(self, path, error):
            if error:
                self.setup_note(f"The update didn't work: {error}", False)
            else:
                event_log("update", "installed")
                self.confirm("Updated", "Restart MnM on Linux now to use the new version?", "Restart",
                             lambda: os.execv(sys.executable, [sys.executable, path]))
            return False

        # ── running the check ────────────────────────────────────────────────
        def run(self, extra, mode="check"):
            if self.proc:
                return
            if self.appimage and "--appimage" not in extra:
                extra = [*extra, "--appimage", self.appimage]
            self.mode = mode
            self.fixes, self.result, self.problem_rows = [], None, []
            self.new_steps = {}   # swapped in when the check finishes, so pages never show a half-done list
            self.report = [f"{TITLE} ({'--' + mode if mode != 'check' else 'check'})"]
            clear(self.todo)
            clear(self.details)
            self.todo_title.set_visible(False)
            busy = {"check": "Checking your system…",
                    "test": "Testing Proton in the game prefix — the first run can take a few minutes…",
                    "watch": "Waiting for you to press Play in the launcher (up to 10 minutes)…",
                    "fix": "Applying the launcher fix…"}[mode]
            self.set_banner("busy", busy)
            self.stop_btn.set_visible(mode in ("test", "watch"))
            for btn in self.buttons.values():
                btn.set_sensitive(False)
            event_log("run-start", f"{mode} {' '.join(extra)}")
            self.script = write_script()
            try:
                self.proc = subprocess.Popen(["bash", self.script, *extra], env=check_env(True),
                                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, bufsize=1,
                                             start_new_session=True)
            except OSError as err:
                self.finish(None, f"Couldn't run the check: {err}")
                return
            threading.Thread(target=self.read_output, args=(self.proc,), daemon=True).start()

        def read_output(self, proc):
            for line in proc.stdout:
                GLib.idle_add(self.on_line, line.rstrip("\n"))
            code = proc.wait()
            GLib.idle_add(self.finish, code, None)

        def stop(self):
            if self.proc and self.proc.poll() is None:
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        def on_focus(self, *_):
            # Back from the terminal or launcher: refresh the list so finished steps disappear.
            # Never after Watch/Test, whose results would be thrown away.
            if (self.win.is_active() and not self.proc and self.fixes and self.last_mode in ("check", "fix")
                    and time.monotonic() - self.last_finished > 3):
                event_log("auto-recheck", "window active again")
                self.run([])

        def on_close(self, *_):
            event_log("window-close")
            self.stop()
            return False

        def on_line(self, line):
            event_log("output", line.replace("\t", " | "))
            kind, _, text = line.partition("\t")
            if not kind.startswith("@@"):
                if line.strip():
                    self.report.append("    " + line.strip())
                    append(self.details, shown(label(line, "detail", selectable=True)))
                return False
            kind = kind[2:]
            if kind == "system":
                os_name, _, gpus = text.partition("\t")
                self.system.set_text(f"{os_name} · GPU: {gpus.strip() or 'unknown'}")
                self.report.append(f"{os_name} · GPU: {gpus.strip() or 'unknown'}")
            elif kind == "section":
                append(self.details, shown(label(text, "section")))
                self.report.append(f"\n{text}")
            elif kind in MARKS:
                mark, cls = MARKS[kind]
                self.report.append(f"  {mark} {text}")
                row = box(False, 6)
                append(row, label(mark, "mark", cls, wrap=False))
                text_lbl = label(text, *(["dim"] if kind == "info" else []), selectable=True)
                text_lbl.set_hexpand(True)
                append(row, text_lbl)
                append(self.details, shown(row))
                if kind in ("bad", "warn"):
                    self.problem_rows.append((kind, text))
                if kind == "info" and self.mode in ("test", "watch"):
                    self.banner_text.set_text(text)
            elif kind == "step":
                key, _, rest = text.partition("\t")
                state, _, detail = rest.partition("\t")
                self.new_steps[key] = (state, detail)
            elif kind == "gpus":
                self.gpu_info = tuple((text.split("\t") + ["", "", ""])[:3])
            elif kind == "fix":
                self.fixes.append(text)
            elif kind == "result":
                self.result = text.split("\t")
            return False

        def finish(self, code, error):
            self.proc = None
            if getattr(self, "script", None):
                try:
                    os.unlink(self.script)
                except OSError:
                    pass
                self.script = None
            self.stop_btn.set_visible(False)
            for btn in self.buttons.values():
                btn.set_sensitive(True)
            self.last_finished, self.last_mode = time.monotonic(), self.mode
            if self.mode == "fix" and not error and code in (0, 1):   # 1 = problems found before fixing
                # The run that applied the fix checked the system *before* fixing it: check again
                self.fix_just_applied = True
                GLib.idle_add(lambda: self.run([]) or False)
                return False
            self.show_fixes()
            if self.new_steps:
                self.steps = self.new_steps
                self.refresh_setup()
                self.refresh_settings()
                self.refresh_running()
            needs_fix = any(f" --fix" in f for f in self.fixes)
            for btn in [self.buttons["fix"]]:
                for cls in ("suggested-action", "primary"):
                    if GTK4:
                        (btn.add_css_class if needs_fix else btn.remove_css_class)(cls)
                    else:
                        ctx = btn.get_style_context()
                        (ctx.add_class if needs_fix else ctx.remove_class)(cls)
            state = self.result[0] if self.result else None
            if error:
                self.set_banner("bad", error)
            elif state == "confirmed":
                self.set_banner("ok", "✓ The launcher can start the game — the link is confirmed.")
            elif state == "ready":
                warn = sum(1 for k, _ in self.problem_rows if k == "warn")
                extra = f" ({warn} warning{'s' if warn != 1 else ''} below.)" if warn else ""
                self.set_banner("ok", "✓ Everything the launcher needs is in place." + extra +
                                "\nTo be sure, press “Watch for Play” and then Play in the launcher.")
            elif state == "problems":
                n = int(self.result[1]) if len(self.result) > 1 else len(self.problem_rows)
                self.set_banner("bad", f"✗ {n} problem{'s' if n != 1 else ''} found — the launcher can't "
                                       f"start the game yet. Do the steps below, then press “Check again”.")
            elif code in (-signal.SIGTERM, 143):
                self.set_banner("bad", "Stopped.")
            else:
                self.set_banner("bad", f"The check ended unexpectedly (exit code {code}). See the details below.")
            if self.fix_just_applied:
                self.fix_just_applied = False
                self.note.set_text("✓ The launcher fix is applied." if state != "problems" else "")
                self.note.set_visible(state != "problems")
                self.set_banner("ok" if state != "problems" else "bad",
                                "✓ Launcher fix applied.\nNext: close the M&M launcher if it's open, then start "
                                "“Monsters & Memories” from your app menu (not the AppImage file) and press "
                                "“Watch for Play” here before pressing Play.")
            event_log("run-end", f"exit {code} | {self.banner_text.get_text()}".replace("\n", " "))
            return False

        def set_banner(self, state, text):
            for s in ("ok", "bad", "busy"):
                if GTK4:
                    self.banner.remove_css_class(f"banner-{s}")
                else:
                    self.banner.get_style_context().remove_class(f"banner-{s}")
            css_class(self.banner, f"banner-{state}")
            self.banner_text.set_text(text)
            if state == "busy":
                self.spinner.start()
                self.spinner.set_visible(True)
            else:
                self.spinner.stop()
                self.spinner.set_visible(False)

        # ── the "What to do" list ────────────────────────────────────────────
        def show_fixes(self):
            clear(self.todo)
            self.todo_title.set_visible(bool(self.fixes))
            for i, fix in enumerate(self.fixes, 1):
                card = box(False, 10, "card")
                append(card, label(f"{i}.", "step-num", wrap=False))
                body = box(True, 6)
                body.set_hexpand(True)
                append(card, body)
                prose, command, note = split_fix(fix)
                if fix.startswith(self_command()):
                    action = "fix" if " --fix" in fix else "test"
                    what = re.search(r"\((.*)\)", fix)
                    append(body, label({"fix": "Apply the launcher fix",
                                        "test": "Run the Proton test"}[action] +
                                       (f" — {what.group(1)}" if what else "") +
                                       (". " + fix.split(") and ", 1)[1].capitalize() + "."
                                        if ") and " in fix else ""), selectable=True))
                    btn = Gtk.Button(label={"fix": "Apply launcher fix…", "test": "Test Proton"}[action])
                    if action == "fix":
                        css_class(btn, "suggested-action", "primary")
                        append(body, label("Press the button below. Afterwards, start the launcher from your app "
                                           "menu (“Monsters & Memories”), not from the AppImage file.", "dim"))
                    btn.connect("clicked", self.on_action, action)
                    btn.set_halign(Gtk.Align.START)
                    append(body, btn)
                else:
                    prose = re.sub(r"\s*\(or pass --appimage[^)]*\)",
                                   " — or press “Choose AppImage…” if it's somewhere else", prose)
                    prose = prose or "Run this command:"
                    append(body, label(prose[0].upper() + prose[1:], selectable=True))
                    if command:
                        row = box(False, 8)
                        cmd = label(command, "cmd", selectable=True)
                        cmd.set_hexpand(True)
                        append(row, cmd)
                        copy = Gtk.Button(label="Copy")
                        copy.set_tooltip_text("Copy this command, then paste it into a terminal")
                        copy.set_valign(Gtk.Align.START)
                        copy.connect("clicked", self.on_copy, command)
                        append(row, copy)
                        append(body, row)
                        hint = "Press Copy, paste it into a terminal (right-click → Paste, or Ctrl+Shift+V) and press Enter"
                        if "sudo " in command:
                            hint += ". It asks for your password; nothing shows while you type it, that's normal"
                        hint += (f". {note[0].upper() + note[1:]}" if note else "")
                        append(body, label(hint + ". Then come back here; the list updates by itself.", "dim"))
                    if "--appimage" in fix:
                        btn = Gtk.Button(label="Choose AppImage…")
                        btn.connect("clicked", self.on_action, "appimage")
                        btn.set_halign(Gtk.Align.START)
                        append(body, btn)
                append(self.todo, shown(card))

        def on_copy(self, button, command):
            event_log("copy-command", command)
            copy_text(command)
            button.set_label("Copied!")
            GLib.timeout_add(1500, lambda: button.set_label("Copy") or False)

        # ── buttons ──────────────────────────────────────────────────────────
        def on_action(self, _button, key):
            event_log("button", key + (" (ignored: check still running)" if self.proc else ""))
            if self.proc:
                return
            if key == "check":
                self.run([])
            elif key == "test":
                self.run(["--test"], "test")
            elif key == "watch":
                self.run(["--watch"], "watch")
            elif key == "fix":
                self.confirm_fix()
            elif key == "appimage":
                self.choose_appimage()
            elif key == "report":
                self.copy_report(_button)

        def copy_report(self, button):
            lines = list(self.report)
            if self.fixes:
                lines.append("\nWhat to do:")
                lines += [f"  {i}. {fix}" for i, fix in enumerate(self.fixes, 1)]
            lines.append("\nResult: " + self.banner_text.get_text().replace("\n", " "))
            copy_text(scrub("\n".join(lines)))
            event_log("copy-report", f"{len(lines)} lines")
            button.set_label("Copied!")
            GLib.timeout_add(1500, lambda: button.set_label("Copy report") or False)

        def confirm_fix(self):
            dialog = Gtk.MessageDialog(transient_for=self.win, modal=True,
                                       message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.OK_CANCEL,
                                       text="Apply the launcher fix?")
            dialog.set_property(
                "secondary-text",
                "This writes three things in your home folder (no sudo needed):\n\n"
                "• ~/.local/share/mnm/bin/umu-run — a small wrapper that removes the AppImage's leaked "
                "Python/GTK settings before starting the real umu-run\n"
                "• ~/.local/share/mnm/mnm-launcher.sh — starts the launcher with that wrapper and saves "
                "its output to ~/.local/share/mnm/launcher.log\n"
                "• Your Monsters & Memories app-menu entry is pointed at that script (the original is "
                "backed up first), or a new entry is created\n\n"
                "Close the launcher first, then start it from the app menu afterwards.")
            dialog.connect("response", self.on_fix_response)
            dialog.present()

        def on_fix_response(self, dialog, response):
            event_log("fix-dialog", "OK" if response == Gtk.ResponseType.OK else "cancel")
            dialog.destroy()
            if response == Gtk.ResponseType.OK:
                self.note.set_text("")
                self.run(["--fix"], "fix")

        def choose_appimage(self):
            chooser = Gtk.FileChooserNative(title="Choose the Monsters & Memories launcher AppImage",
                                            transient_for=self.win, action=Gtk.FileChooserAction.OPEN,
                                            accept_label="Use this", cancel_label="Cancel")
            filt = Gtk.FileFilter()
            filt.set_name("AppImage files")
            for pattern in ("*.appimage", "*.AppImage", "*.APPIMAGE"):
                filt.add_pattern(pattern)
            chooser.add_filter(filt)
            chooser.connect("response", self.on_appimage_chosen)
            self.chooser = chooser
            chooser.show()

        def on_appimage_chosen(self, chooser, response):
            path = chooser.get_file().get_path() if response == Gtk.ResponseType.ACCEPT and chooser.get_file() else None
            chooser.destroy()
            self.chooser = None
            event_log("appimage-chosen", path or "(cancelled)")
            if path:
                self.appimage = path
                self.run([])

    flags = getattr(Gio.ApplicationFlags, "DEFAULT_FLAGS", Gio.ApplicationFlags.FLAGS_NONE)
    app = Gtk.Application(application_id=APP_ID, flags=flags)
    windows = []

    def activate(application):
        if windows:
            windows[0].win.present()
            return
        load_css(CSS)
        windows.append(Window(application))

    app.connect("activate", activate)
    return app.run(None)


if __name__ == "__main__":
    sys.exit(main())
