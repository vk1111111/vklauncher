from __future__ import annotations

import subprocess
import threading
import traceback
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen, ModalScreen
from textual.widgets import (
    Header,
    Footer,
    Button,
    Static,
    ListView,
    ListItem,
    Label,
    Input,
    RadioSet,
    RadioButton,
    ProgressBar,
    Log,
    Select,
    DataTable,
)
from textual.binding import Binding

from . import config, versions, instances, auth, modrinth
from . import launch as launch_mod

def safe_markup(text: str) -> str:
    return text.replace("[", r"\[")


APP_CSS = """
Screen {
    background: $surface;
}
#title-bar {
    height: 1;
    content-align: center middle;
    color: $text-muted;
}
.panel {
    border: round $primary;
    padding: 1 2;
}
.dim {
    color: $text-muted;
}
#status-bar {
    height: 1;
    dock: bottom;
    background: $panel;
    color: $text-muted;
    padding: 0 1;
}
Button {
    min-width: 14;
}
.form-row {
    height: 3;
    margin-bottom: 1;
}
"""


def fmt_ts(ts: float) -> str:
    if not ts:
        return "never"
    import datetime

    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

class ProgressModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "noop", "", show=False)]

    def __init__(self, title: str):
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="progress-box"):
            yield Static(self._title, id="progress-title")
            yield Static("Starting...", id="progress-label")
            yield ProgressBar(total=100, id="progress-bar", show_eta=False)
            yield Log(id="progress-log", max_lines=200)

    def action_noop(self) -> None:
        pass

    def update_progress(self, label: str, done: int, total: int) -> None:
        try:
            bar = self.query_one("#progress-bar", ProgressBar)
            bar.update(total=max(total, 1), progress=done)
            self.query_one("#progress-label", Static).update(f"{label}  ({done}/{total})")
        except Exception:
            pass

    def log_line(self, text: str) -> None:
        try:
            self.query_one("#progress-log", Log).write_line(text)
        except Exception:
            pass


class MessageModal(ModalScreen[None]):
    def __init__(self, title: str, message: str, is_error: bool = False):
        super().__init__()
        self._title = title
        self._message = message
        self._is_error = is_error

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="msg-box"):
            style = "bold red" if self._is_error else "bold green"
            yield Static(f"[{style}]{safe_markup(self._title)}[/]")
            yield Static(safe_markup(self._message))
            yield Button("OK", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    def __init__(self, question: str):
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="confirm-box"):
            yield Static(self._question)
            with Horizontal():
                yield Button("Yes", id="yes", variant="error")
                yield Button("No", id="no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

class AddOfflineModal(ModalScreen[Optional[str]]):
    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="offline-box"):
            yield Static("[bold]Add offline account[/]")
            yield Input(placeholder="Username (3-16 chars)", id="username")
            with Horizontal():
                yield Button("Add", id="add", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add":
            name = self.query_one("#username", Input).value.strip()
            self.dismiss(name or None)
        else:
            self.dismiss(None)


class DeviceCodeModal(ModalScreen[None]):
    def __init__(self, client_id: str | None = None):
        super().__init__()
        self.client_id = client_id or auth.resolve_ms_client_id()
        self.result_account: Optional[auth.Account] = None
        self.error: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="device-box"):
            yield Static("[bold]Sign in with Microsoft[/]")
            yield Static("Contacting Microsoft...", id="device-status")
            yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.run_worker(self._do_login, thread=True)

    def _do_login(self) -> None:
        try:
            payload = auth.start_device_code(self.client_id)
            url = payload.get("verification_uri", "https://microsoft.com/link")
            code = payload.get("user_code", "?")
            msg = (
                f"1. Open: [bold]{url}[/]\n"
                f"2. Enter code: [bold yellow]{code}[/]\n\n"
                f"Waiting for you to finish signing in..."
            )
            self.app.call_from_thread(self._set_status, msg)

            interval = payload.get("interval", 5)
            expires_in = payload.get("expires_in", 900)
            token_payload = auth.poll_device_code(
                self.client_id, payload["device_code"], interval, expires_in
            )
            account = auth.complete_login_from_ms_tokens(token_payload)
            auth.save_account(account, make_active=True)
            self.result_account = account
            self.app.call_from_thread(self.dismiss, None)
        except Exception as e:  
            self.error = str(e)
            self.app.call_from_thread(self._set_status, f"[red]Error: {safe_markup(str(e))}[/]")

    def _set_status(self, text: str) -> None:
        self.query_one("#device-status", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class AccountsScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("n", "add_offline", "Offline account"),
        Binding("m", "add_microsoft", "Microsoft account"),
        Binding("d", "delete_account", "Delete"),
        Binding("enter", "set_active", "Set active"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Accounts", id="title-bar")
        yield ListView(id="account-list")
        yield Static(
            "[n] add offline account   [m] sign in with Microsoft   "
            "[enter] set active   [d] delete   [esc] back",
            id="status-bar",
        )
        yield Footer()

    def on_screen_resume(self) -> None:
        self.refresh_list()

    def refresh_list(self) -> None:
        lv = self.query_one("#account-list", ListView)
        lv.clear()
        data = auth._load_all()
        active = data.get("active", "")
        for acc in auth.list_accounts():
            marker = "[green]● active[/]" if acc.username == active else ""
            label = f"{safe_markup(acc.username)}  ({acc.kind})  {marker}"
            lv.append(ListItem(Label(label), name=acc.username))

    def action_add_offline(self) -> None:
        def cb(name: Optional[str]) -> None:
            if not name:
                return
            try:
                auth.add_offline_account(name)
                self.refresh_list()
            except Exception as e:
                self.app.push_screen(MessageModal("Error", str(e), is_error=True))

        self.app.push_screen(AddOfflineModal(), cb)

    def action_add_microsoft(self) -> None:
        def cb(_: None) -> None:
            self.refresh_list()

        self.app.push_screen(DeviceCodeModal(), cb)

    def action_set_active(self) -> None:
        lv = self.query_one("#account-list", ListView)
        if lv.highlighted_child is None:
            return
        name = lv.highlighted_child.name
        if name:
            auth.set_active_account(name)
            self.refresh_list()

    def action_delete_account(self) -> None:
        lv = self.query_one("#account-list", ListView)
        if lv.highlighted_child is None:
            return
        name = lv.highlighted_child.name

        def cb(confirmed: bool) -> None:
            if confirmed and name:
                auth.remove_account(name)
                self.refresh_list()

        if name:
            self.app.push_screen(ConfirmModal(f"Delete account '{name}'?"), cb)

class SettingsScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def compose(self) -> ComposeResult:
        s = config.load_settings()
        yield Header()
        yield Static("Settings", id="title-bar")
        with VerticalScroll(classes="panel"):
            yield Label("Default min RAM (MB)")
            yield Input(value=str(s["min_ram_mb"]), id="min_ram")
            yield Label("Default max RAM (MB)")
            yield Input(value=str(s["max_ram_mb"]), id="max_ram")
            yield Label("Java path (leave blank to automatically detect a path")
            yield Input(value=s["java_path"], id="java_path")
            yield Label("Extra JVM args")
            yield Input(value=s["extra_jvm_args"], id="extra_jvm_args")
            yield Label("Microsoft OAuth client id override (blank = built-in)")
            yield Input(value=s["ms_client_id"], id="ms_client_id")
            yield Button("Save (ctrl+s)", id="save", variant="primary")
        yield Static("[ctrl+s] Save   [esc] Exit without saving", id="status-bar")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()

    def action_save(self) -> None:
        s = config.load_settings()
        try:
            s["min_ram_mb"] = int(self.query_one("#min_ram", Input).value)
            s["max_ram_mb"] = int(self.query_one("#max_ram", Input).value)
        except ValueError:
            self.app.push_screen(MessageModal("Error", "RAM values must be numbers.", is_error=True))
            return
        s["java_path"] = self.query_one("#java_path", Input).value.strip()
        s["extra_jvm_args"] = self.query_one("#extra_jvm_args", Input).value
        s["ms_client_id"] = self.query_one("#ms_client_id", Input).value.strip()
        config.save_settings(s)
        self.app.pop_screen()


class InstanceSettingsModal(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, inst: instances.Instance):
        super().__init__()
        self.inst = inst

    def compose(self) -> ComposeResult:
        settings = config.load_settings()
        defaults = config.default_launcher_settings()
        default_min = settings.get("min_ram_mb", defaults.get("min_ram_mb", 1024))
        default_max = settings.get("max_ram_mb", defaults.get("max_ram_mb", 4096))
        inst_settings = instances.load_instance_settings(self.inst)
        min_raw = inst_settings.get("min_ram_mb")
        max_raw = inst_settings.get("max_ram_mb")
        min_val = "" if min_raw is None else str(min_raw)
        max_val = "" if max_raw is None else str(max_raw)
        with Vertical(classes="panel", id="instance-settings-box"):
            yield Static(f"[bold]Instance settings - {safe_markup(self.inst.name)}[/]")
            yield Label(f"Min RAM (MB) - leave blank to use default ({default_min})")
            yield Input(value=min_val, placeholder=str(default_min), id="inst_min_ram")
            yield Label(f"Max RAM (MB) - leave blank to use default ({default_max})")
            yield Input(value=max_val, placeholder=str(default_max), id="inst_max_ram")
            with Horizontal():
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def _parse_ram(self, raw: str) -> Optional[int]:
        raw = raw.strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError as e:
            raise ValueError("RAM values must be whole numbers in MB.") from e
        if value < 256:
            raise ValueError("RAM must be at least 256 MB.")
        return value

    def _save(self) -> None:
        try:
            min_ram = self._parse_ram(self.query_one("#inst_min_ram", Input).value)
            max_ram = self._parse_ram(self.query_one("#inst_max_ram", Input).value)
        except ValueError as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))
            return

        settings = config.load_settings()
        defaults = config.default_launcher_settings()
        effective_min = (
            min_ram if min_ram is not None else settings.get("min_ram_mb", defaults.get("min_ram_mb", 1024))
        )
        effective_max = (
            max_ram if max_ram is not None else settings.get("max_ram_mb", defaults.get("max_ram_mb", 4096))
        )
        if effective_min > effective_max:
            self.app.push_screen(
                MessageModal("Error", "Min RAM cannot be greater than max RAM.", is_error=True)
            )
            return

        inst_settings = instances.load_instance_settings(self.inst)
        inst_settings["min_ram_mb"] = min_ram
        inst_settings["max_ram_mb"] = max_ram
        instances.save_instance_settings(self.inst, inst_settings)
        self.dismiss(True)


class NewInstanceScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("New Instance", id="title-bar")
        with VerticalScroll(classes="panel"):
            yield Label("Instance name")
            yield Input(placeholder="e.g. Survival 1.20", id="name")
            yield Label("Mod loader")
            yield RadioSet(
                RadioButton("Vanilla", value=True, id="loader_vanilla"),
                RadioButton("Fabric", id="loader_fabric"),
                RadioButton("Quilt", id="loader_quilt"),
                id="loader_set",
            )
            yield Label("Show snapshots")
            yield RadioSet(
                RadioButton("Releases only", value=True, id="filter_release"),
                RadioButton("Include snapshots", id="filter_all"),
                id="filter_set",
            )
            yield Label("Minecraft version")
            yield Select([], id="version_select", allow_blank=True)
            yield Button("Create & Download", id="create", variant="primary")
        yield Static("Loading version list...", id="status-bar")
        yield Footer()

    def on_screen_resume(self) -> None:
        self._load_versions()

    @work(thread=True)
    def _load_versions(self) -> None:
        try:
            entries, latest = versions.fetch_version_manifest()
        except Exception as e:
            self.app.call_from_thread(self._set_status, f"[red]Failed to load versions: {safe_markup(str(e))}[/]")
            return
        self._all_entries = entries
        self.app.call_from_thread(self._populate, "release")

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status-bar", Static).update(text)
        except Exception:
            pass

    def _populate(self, filter_type: str) -> None:
        entries = getattr(self, "_all_entries", [])
        if filter_type == "release":
            filtered = [e for e in entries if e.type == "release"]
        else:
            filtered = entries
        options = [(f"{e.id}  ({e.type})", e.id) for e in filtered[:200]]
        select = self.query_one("#version_select", Select)
        select.set_options(options)
        self._set_status(f"{len(filtered)} versions loaded. Fill in the form and create.")

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "filter_set":
            filter_type = "release" if event.pressed.id == "filter_release" else "all"
            self._populate(filter_type)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "create":
            return
        name = self.query_one("#name", Input).value.strip()
        version_select = self.query_one("#version_select", Select)
        mc_version = version_select.value
        loader_set = self.query_one("#loader_set", RadioSet)
        pressed = loader_set.pressed_button
        loader = "vanilla"
        if pressed and pressed.id == "loader_fabric":
            loader = "fabric"
        elif pressed and pressed.id == "loader_quilt":
            loader = "quilt"

        if not name:
            self.app.push_screen(MessageModal("Error", "Missing instance name.", is_error=True))
            return
        if not mc_version or mc_version == Select.BLANK:
            self.app.push_screen(MessageModal("Error", "Minecraft version missing.", is_error=True))
            return

        self._create_instance(name, mc_version, loader)

    def _create_instance(self, name: str, mc_version: str, loader: str) -> None:
        modal = ProgressModal(f"Creating '{name}'")
        self.app.push_screen(modal)
        self._do_create(name, mc_version, loader, modal)

    @work(thread=True)
    def _do_create(self, name: str, mc_version: str, loader: str, modal: ProgressModal) -> None:
        def progress(label: str, done: int, total: int) -> None:
            self.app.call_from_thread(modal.update_progress, label, done, total)

        try:
            if loader == "fabric":
                vjson = versions.install_fabric(mc_version, progress=progress)
            elif loader == "quilt":
                vjson = versions.install_quilt(mc_version, progress=progress)
            else:
                vjson = versions.full_install(mc_version, progress=progress)

            inst = instances.create_instance(
                name=name,
                mc_version=mc_version,
                loader=loader,
                loader_version="",
                version_profile_id=vjson["id"],
            )
            self.app.call_from_thread(self._on_success, modal, inst.name)
        except Exception as e: 
            self.app.call_from_thread(self._on_failure, modal, str(e))

    def _on_success(self, modal: ProgressModal, name: str) -> None:
        self.app.pop_screen()
        self.app.pop_screen()
        self.app.push_screen(MessageModal("Success", f"Instance '{name}' is ready to launch."))
        main_screen = self.app.get_screen("main")
        if hasattr(main_screen, "refresh_list"):
            main_screen.refresh_list()

    def _on_failure(self, modal: ProgressModal, error: str) -> None:
        self.app.pop_screen()
        self.app.push_screen(MessageModal("Failed to create instance", error, is_error=True))

class PickPackVersionModal(ModalScreen[Optional[modrinth.PackVersion]]):
    def __init__(self, pack: modrinth.ModpackHit, pack_versions: list[modrinth.PackVersion]):
        super().__init__()
        self.pack = pack
        self.pack_versions = pack_versions

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="pick-version-box"):
            yield Static(f"[bold]{safe_markup(self.pack.title)}[/] - choose a version")
            options = [
                (f"{safe_markup(v.name)}  ({', '.join(v.game_versions[:1])}, {', '.join(v.loaders)})", v.id)
                for v in self.pack_versions
            ]
            yield Select(options, id="pv_select", allow_blank=False)
            yield Label("Instance name")
            yield Input(value=self.pack.title, id="pv_name")
            with Horizontal():
                yield Button("Install", id="install", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "install":
            self.dismiss(None)
            return
        select = self.query_one("#pv_select", Select)
        chosen_id = select.value
        pv = next((v for v in self.pack_versions if v.id == chosen_id), None)
        if pv is None:
            self.dismiss(None)
            return
        name_input = self.query_one("#pv_name", Input).value.strip() or self.pack.title
        pv_named = modrinth.PackVersion(**{**pv.__dict__, "name": pv.name})
        self._chosen_name = name_input
        self.dismiss(pv_named)


class ModrinthScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "install_selected", "Install"),
        Binding("/", "focus_search", "Search"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Modrinth Modpacks", id="title-bar")
        yield Input(placeholder="Search modpacks (press enter)...", id="search")
        yield ListView(id="results")
        yield Static(
            "[bold]/[/bold] search   [enter] install selected pack   [esc] back",
            id="status-bar",
        )
        yield Footer()

    def on_screen_resume(self) -> None:
        self._results: list[modrinth.ModpackHit] = []
        self.query_one("#search", Input).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._install_slug(event.item.name)

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self._do_search(event.value)

    @work(thread=True)
    def _do_search(self, query: str) -> None:
        try:
            hits = modrinth.search_modpacks(query or "modpack")
        except Exception as e:
            self.app.call_from_thread(
                self.app.push_screen, MessageModal("Search failed", str(e), is_error=True)
            )
            return
        self._results = hits
        self.app.call_from_thread(self._populate_results, hits)

    @work
    async def _populate_results(self, hits: list[modrinth.ModpackHit]) -> None:
        lv = self.query_one("#results", ListView)
        await lv.clear()
        items = [
            ListItem(
                Label(
                    f"{safe_markup(h.title)}  by {safe_markup(h.author)}   ↓{h.downloads:,}\n"
                    f"  {safe_markup(h.description[:90])}"
                ),
                name=h.slug,
            )
            for h in hits
        ]
        if items:
            await lv.extend(items)
            lv.index = 0
            lv.focus()

    def action_install_selected(self) -> None:
        lv = self.query_one("#results", ListView)
        if lv.highlighted_child is None:
            return
        self._install_slug(lv.highlighted_child.name)

    def _install_slug(self, slug: Optional[str]) -> None:
        if not slug:
            return
        pack = next((h for h in self._results if h.slug == slug), None)
        if not pack:
            return
        self._fetch_versions_then_pick(pack)

    @work(thread=True)
    def _fetch_versions_then_pick(self, pack: modrinth.ModpackHit) -> None:
        try:
            pvs = modrinth.get_project_versions(pack.project_id or pack.slug)
        except Exception as e: 
            self.app.call_from_thread(
                self.app.push_screen, MessageModal("Error", str(e), is_error=True)
            )
            return
        if not pvs:
            self.app.call_from_thread(
                self.app.push_screen, MessageModal("Error", "No versions found for this pack.", is_error=True)
            )
            return

        modal = PickPackVersionModal(pack, pvs)

        def cb(chosen: Optional[modrinth.PackVersion]) -> None:
            if chosen is None:
                return
            name = getattr(modal, "_chosen_name", pack.title)
            self._install(chosen, name)

        self.app.call_from_thread(self.app.push_screen, modal, cb)

    def _install(self, pack_version: modrinth.PackVersion, name: str) -> None:
        modal = ProgressModal(f"Installing '{name}'")
        self.app.push_screen(modal)
        self._do_install(pack_version, name, modal)

    @work(thread=True)
    def _do_install(self, pack_version: modrinth.PackVersion, name: str, modal: ProgressModal) -> None:
        def progress(label: str, done: int, total: int) -> None:
            self.app.call_from_thread(modal.update_progress, label, done, total)

        try:
            inst = modrinth.install_modpack(pack_version, name, progress=progress)
            self.app.call_from_thread(self._on_success, modal, inst.name)
        except Exception as e:  
            self.app.call_from_thread(self._on_failure, modal, str(e))

    def _on_success(self, modal: ProgressModal, name: str) -> None:
        self.app.pop_screen()
        self.app.push_screen(MessageModal("Success", f"Modpack installed as instance '{name}'."))
        main_screen = self.app.get_screen("main")
        if hasattr(main_screen, "refresh_list"):
            main_screen.refresh_list()

    def _on_failure(self, modal: ProgressModal, error: str) -> None:
        self.app.pop_screen()
        self.app.push_screen(MessageModal("Install failed", error, is_error=True))

class ConsoleScreen(Screen):
    BINDINGS = [Binding("escape", "close", "Close (game keeps running)")]

    def __init__(self, instance_name: str, proc: subprocess.Popen):
        super().__init__()
        self.instance_name = instance_name
        self.proc = proc

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"Running: {self.instance_name}", id="title-bar")
        yield Log(id="console-log", max_lines=5000)
        yield Static("[esc] close this view (game keeps running in the background)", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._read_output()

    @work(thread=True)
    def _read_output(self) -> None:
        def emit(line: str) -> None:
            try:
                self.app.call_from_thread(self.query_one("#console-log", Log).write_line, line)
            except Exception:
                pass

        try:
            launch_mod.stream_output(self.proc, emit)
        except Exception as e:  
            emit(f"[launcher] output stream ended: {e}")
        emit(f"[launcher] process exited with code {self.proc.poll()}")

    def action_close(self) -> None:
        self.app.pop_screen()

class MainScreen(Screen):
    BINDINGS = [
        Binding("n", "new_instance", "New"),
        Binding("enter", "launch_instance", "Launch", show=False),
        Binding("x", "browse_files", "Browse files"),
        Binding("d", "delete_instance", "Delete"),
        Binding("r", "rename_instance", "Rename"),
        Binding("a", "accounts", "Accounts"),
        Binding("p", "modrinth", "Search Modrinth packs"),
        Binding("s", "instance_settings", "Instance settings"),
        Binding("g", "settings", "Launcher settings"),
        Binding("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("vklauncher", id="title-bar")
        with Horizontal():
            with Vertical(classes="panel", id="left-panel"):
                yield Label("Instances")
                yield DataTable(id="instance-table")
            with Vertical(classes="panel", id="right-panel"):
                yield Label("Active account")
                yield Static("(none)", id="active-account")
                yield Label("")
                yield Static("Instance Type: -", id="detail-type")
                yield Static("Source: -", id="detail-source")
                yield Static("Minimum memory allocated: -", id="detail-min-ram")
                yield Static("Maximum memory allocated: -", id="detail-max-ram")
                yield Static("JDK: -", id="detail-jdk")
                yield Static("Version: -", id="detail-version")
                
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#instance-table", DataTable)
        table.add_columns("Name", "Version", "Loader", "Last played")
        table.cursor_type = "row"
        self.refresh_list()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_launch_instance()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._refresh_instance_details()

    def on_screen_resume(self) -> None:
        self.refresh_list()

    def refresh_list(self) -> None:
        table = self.query_one("#instance-table", DataTable)
        table.clear()
        self._instances = instances.list_instances()
        for inst in sorted(self._instances, key=lambda i: -i.last_played):
            table.add_row(inst.name, inst.mc_version, inst.loader, fmt_ts(inst.last_played), key=inst.name)

        acc = auth.get_active_account()
        label = f"{safe_markup(acc.username)} ({acc.kind})" if acc else "(none - press [a] to add one)"
        self.query_one("#active-account", Static).update(label)
        self._refresh_instance_details()

    def _refresh_instance_details(self) -> None:
        inst = self._selected_instance()
        settings = config.load_settings()
        if not inst:
            self.query_one("#detail-type", Static).update("Instance Type: -")
            self.query_one("#detail-source", Static).update("Source: -")
            self.query_one("#detail-min-ram", Static).update("Minimum memory allocated: -")
            self.query_one("#detail-max-ram", Static).update("Maximum memory allocated: -")
            self.query_one("#detail-jdk", Static).update("JDK: -")
            self.query_one("#detail-version", Static).update("Version: -")
            return

        loader = inst.loader or "vanilla"
        if inst.loader_version:
            inst_type = f"{loader} {inst.loader_version}"
        else:
            inst_type = loader

        if inst.modpack_source.startswith("modrinth:"):
            source = f"Modrinth ({inst.modpack_source.split(':', 1)[1]})"
        elif inst.modpack_source:
            source = inst.modpack_source
        else:
            source = "local"

        inst_settings = instances.load_instance_settings(inst)
        defaults = config.default_launcher_settings()
        min_ram = inst_settings.get("min_ram_mb")
        if min_ram is None:
            min_ram = settings.get("min_ram_mb", defaults.get("min_ram_mb", 1024))
        max_ram = inst_settings.get("max_ram_mb")
        if max_ram is None:
            max_ram = settings.get("max_ram_mb", defaults.get("max_ram_mb", 4096))
        jdk = config.find_java(inst_settings.get("java_path") or None) or "auto"
        min_note = "" if inst_settings.get("min_ram_mb") is not None else " (default)"
        max_note = "" if inst_settings.get("max_ram_mb") is not None else " (default)"

        self.query_one("#detail-type", Static).update(f"Instance Type: {safe_markup(inst_type)}")
        self.query_one("#detail-source", Static).update(f"Source: {safe_markup(source)}")
        self.query_one("#detail-min-ram", Static).update(
            f"Minimum memory allocated: {min_ram} MB{min_note}"
        )
        self.query_one("#detail-max-ram", Static).update(
            f"Maximum memory allocated: {max_ram} MB{max_note}"
        )
        self.query_one("#detail-jdk", Static).update(f"JDK: {safe_markup(jdk)}")
        self.query_one("#detail-version", Static).update(f"Version: {safe_markup(inst.mc_version)}")

    def _selected_instance(self) -> Optional[instances.Instance]:
        table = self.query_one("#instance-table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        name = row_key.value
        return instances.get_instance(name) if name else None

    def action_new_instance(self) -> None:
        self.app.push_screen(NewInstanceScreen())

    def action_accounts(self) -> None:
        self.app.push_screen(AccountsScreen())

    def action_modrinth(self) -> None:
        self.app.push_screen(ModrinthScreen())

    def action_instance_settings(self) -> None:
        inst = self._selected_instance()
        if not inst:
            self.app.push_screen(
                MessageModal("No instance", "Select an instance first.", is_error=True)
            )
            return

        def cb(saved: bool) -> None:
            if saved:
                self._refresh_instance_details()

        self.app.push_screen(InstanceSettingsModal(inst), cb)

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def action_browse_files(self) -> None:
        inst = self._selected_instance()
        if not inst:
            self.app.push_screen(
                MessageModal("No instance", "Select an instance first.", is_error=True)
            )
            return
        try:
            config.open_directory(inst.minecraft_dir)
        except Exception as e:  # noqa: BLE001
            self.app.push_screen(
                MessageModal("Could not open folder", str(e), is_error=True)
            )

    def action_delete_instance(self) -> None:
        inst = self._selected_instance()
        if not inst:
            return

        def cb(confirmed: bool) -> None:
            if confirmed:
                instances.delete_instance(inst.name)
                self.refresh_list()

        self.app.push_screen(ConfirmModal(f"Delete instance '{inst.name}' and all its data?"), cb)

    def action_rename_instance(self) -> None:
        inst = self._selected_instance()
        if not inst:
            return

        class RenameModal(ModalScreen[Optional[str]]):
            def compose(self_inner) -> ComposeResult:
                with Vertical(classes="panel"):
                    yield Static(f"Rename '{inst.name}'")
                    yield Input(value=inst.name, id="new_name")
                    with Horizontal():
                        yield Button("Rename", id="ok", variant="primary")
                        yield Button("Cancel", id="cancel")

            def on_button_pressed(self_inner, event: Button.Pressed) -> None:
                if event.button.id == "ok":
                    self_inner.dismiss(self_inner.query_one("#new_name", Input).value.strip())
                else:
                    self_inner.dismiss(None)

        def cb(new_name: Optional[str]) -> None:
            if new_name and new_name != inst.name:
                try:
                    instances.rename_instance(inst.name, new_name)
                    self.refresh_list()
                except Exception as e:
                    self.app.push_screen(MessageModal("Error", str(e), is_error=True))

        self.app.push_screen(RenameModal(), cb)

    def action_launch_instance(self) -> None:
        inst = self._selected_instance()
        if not inst:
            self.app.push_screen(MessageModal("No instance", "Select an instance first.", is_error=True))
            return
        account = auth.get_active_account()
        if not account:
            self.app.push_screen(
                MessageModal("No account", "Add and select an account first (press [a]).", is_error=True)
            )
            return
        self._launch(inst, account)

    @work(thread=True)
    def _launch(self, inst: instances.Instance, account: auth.Account) -> None:
        settings = config.load_settings()
        try:
            if account.kind == "microsoft":
                account = auth.ensure_fresh(account, auth.resolve_ms_client_id(settings))
            proc = launch_mod.launch(inst, account, settings)
            instances.touch_last_played(inst.name)
            self.app.call_from_thread(self._open_console, inst.name, proc)
        except Exception as e:  
            tb = traceback.format_exc()
            self.app.call_from_thread(
                self.app.push_screen, MessageModal("Launch failed", f"{e}\n\n{tb[-600:]}", is_error=True)
            )

    def _open_console(self, name: str, proc: subprocess.Popen) -> None:
        self.refresh_list()
        self.app.push_screen(ConsoleScreen(name, proc))


class MCLauncherApp(App):
    CSS = APP_CSS
    TITLE = "vklauncher"

    def on_mount(self) -> None:
        config.ensure_dirs()
        self.install_screen(MainScreen(), name="main")
        self.push_screen("main")


def run() -> None:
    config.ensure_dirs()
    app = MCLauncherApp()
    app.run()