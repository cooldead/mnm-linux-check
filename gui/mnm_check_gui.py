#!/usr/bin/env python3
"""Monsters & Memories — Linux launcher check (GUI).

Runs the launcher check and shows what passed, what's broken and exactly what to do
about it, with copy buttons for commands. Needs Python 3 + PyGObject (GTK 4 or 3);
without them, or with --cli, it runs the same check in the terminal.

    python3 mnm-linux-check.py            open the window
    python3 mnm-linux-check.py --cli ...  terminal check (same options as the .sh: --test, --watch, --fix, --appimage PATH)
    add --report to print it without personal data (home paths, user/host names, tokens), for bug reports

MNM_CHECK_EVENTLOG=FILE appends a timestamped log of everything done in the window
(buttons, dialogs, chosen files, check output) to FILE, with personal data removed.
"""
import getpass
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

# build.sh replaces this with the full mnm-linux-check.sh, making this file self-contained.
CHECK_SCRIPT = r'''@@CHECK_SCRIPT@@'''

TITLE = "Monsters & Memories — Linux Check"
APP_ID = "io.github.mnm.LinuxCheck"


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
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(css)
        elif GTK4:
            provider.load_from_data(css, -1)
        else:
            provider.load_from_data(css.encode())
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

            self.win = Gtk.ApplicationWindow(application=app, title=TITLE)
            self.win.set_default_size(820, 780)
            self.win.connect("close-request" if GTK4 else "delete-event", self.on_close)
            self.win.connect("notify::is-active", self.on_focus)

            outer = box(True, 10)
            for side in ("top", "bottom", "start", "end"):
                getattr(outer, f"set_margin_{side}")(16)

            append(outer, label("Monsters & Memories — Linux Check", "title"))
            self.system = label("Checks that the official Linux launcher can start the game, "
                                "and tells you exactly what to do if it can't.", "dim")
            append(outer, self.system)

            self.banner = box(False, 10, "banner", "banner-busy")
            self.spinner = Gtk.Spinner()
            append(self.banner, self.spinner)
            self.banner_text = label("Starting…", "banner-text")
            self.banner_text.set_hexpand(True)
            append(self.banner, self.banner_text)
            self.stop_btn = Gtk.Button(label="Stop")
            self.stop_btn.connect("clicked", lambda *_: self.stop())
            append(self.banner, self.stop_btn)
            append(outer, self.banner)

            scroller = Gtk.ScrolledWindow()
            scroller.set_vexpand(True)
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            content = box(True, 12)
            content.set_margin_end(6)
            set_child(scroller, content)
            append(outer, scroller)

            self.todo_title = label("What to do", "section")
            self.todo = box(True, 8)
            append(content, self.todo_title)
            append(content, self.todo)

            self.details_title = label("Details", "section")
            self.details = box(True, 3)
            append(content, self.details_title)
            append(content, self.details)

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
                                                 "if it isn't in ~/Applications, ~/Downloads or similar."),
                ("report", "Copy report", "Copy the full result as text, to paste into a bug report or Discord. "
                                          "Personal data (login token, user and computer name, home folder path) is left out."),
            ):
                btn = Gtk.Button(label=text)
                btn.set_tooltip_text(tip)
                btn.connect("clicked", self.on_action, key)
                append(actions, btn)
                self.buttons[key] = btn
            append(outer, actions)
            self.action_hint = label("Hover a button to see what it does. Nothing is installed or changed "
                                     "unless you press “Apply launcher fix…” and confirm.", "dim")
            append(outer, self.action_hint)

            set_child(self.win, outer)
            shown(self.win)
            self.win.present()
            event_log("window-open", f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}")
            mode = next((m for m in ("fix", "test", "watch") if f"--{m}" in args), "check")
            self.run(args, mode)

        # ── running the check ────────────────────────────────────────────────
        def run(self, extra, mode="check"):
            if self.proc:
                return
            if self.appimage and "--appimage" not in extra:
                extra = [*extra, "--appimage", self.appimage]
            self.mode = mode
            self.fixes, self.result, self.problem_rows = [], None, []
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
            needs_fix = any(f" --fix" in f for f in self.fixes)
            for btn in [self.buttons["fix"]]:
                if GTK4:
                    (btn.add_css_class if needs_fix else btn.remove_css_class)("suggested-action")
                else:
                    ctx = btn.get_style_context()
                    (ctx.add_class if needs_fix else ctx.remove_class)("suggested-action")
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
                        css_class(btn, "suggested-action")
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
