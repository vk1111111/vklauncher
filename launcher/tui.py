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
            yield Static(f"[{style}]{self._title}[/]")
            yield Static(self._message)
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
            opened = auth.open_device_code_browser(payload)
            if opened:
                msg = (
                    f"A browser window should have opened for Microsoft sign-in.\n"
                    f"If it didn't, open [bold]{url}[/] and enter:\n"
                    f"[bold yellow]{code}[/]\n\n"
                    f"Waiting for you to finish signing in..."
                )
            else:
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
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            self.app.call_from_thread(self._set_status, f"[red]Error: {e}[/]")

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
            label = f"{acc.username}  [{acc.kind}]  {marker}"
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
            yield Label("Min RAM (MB)")
            yield Input(value=str(s["min_ram_mb"]), id="min_ram")
            yield Label("Max RAM (MB)")
            yield Input(value=str(s["max_ram_mb"]), id="max_ram")
            yield Label("Java path (blank = auto-detect)")
            yield Input(value=s["java_path"], id="java_path")
            yield Label("Extra JVM args")
            yield Input(value=s["extra_jvm_args"], id="extra_jvm_args")
            yield Label("Microsoft OAuth client id override (blank = built-in)")
            yield Input(value=s["ms_client_id"], id="ms_client_id")
            yield Button("Save (ctrl+s)", id="save", variant="primary")
        yield Static("[ctrl+s] save   [esc] back without saving", id="status-bar")
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
            self.app.call_from_thread(self._set_status, f"[red]Failed to load versions: {e}[/]")
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
            self.app.push_screen(MessageModal("Error", "Please enter an instance name.", is_error=True))
            return
        if not mc_version or mc_version == Select.BLANK:
            self.app.push_screen(MessageModal("Error", "Please choose a Minecraft version.", is_error=True))
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

# modrinth experimental
class PickPackVersionModal(ModalScreen[Optional[modrinth.PackVersion]]):
    def __init__(self, pack: modrinth.ModpackHit, pack_versions: list[modrinth.PackVersion]):
        super().__init__()
        self.pack = pack
        self.pack_versions = pack_versions

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel", id="pick-version-box"):
            yield Static(f"[bold]{self.pack.title}[/] — choose a version")
            options = [
                (f"{v.name}  ({', '.join(v.game_versions[:1])}, {', '.join(v.loaders)})", v.id)
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
            "[/] search   [enter] Install selected pack   [esc] Back",
            id="status-bar",
        )
        yield Footer()

    def on_screen_resume(self) -> None:
        self._results: list[modrinth.ModpackHit] = []
        self.query_one("#search", Input).focus()

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

    def _populate_results(self, hits: list[modrinth.ModpackHit]) -> None:
        lv = self.query_one("#results", ListView)
        lv.clear()
        for h in hits:
            label = f"{h.title}  by {h.author}   ↓{h.downloads:,}\n  {h.description[:90]}"
            lv.append(ListItem(Label(label), name=h.slug))

    def action_install_selected(self) -> None:
        lv = self.query_one("#results", ListView)
        if lv.highlighted_child is None:
            return
        slug = lv.highlighted_child.name
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
                self.app.push_screen, MessageModal("Error", "No versions found.", is_error=True)
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
        except Exception as e:  # noqa: BLE001
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
    BINDINGS = [Binding("escape", "close", "Close logs")]

    def __init__(self, instance_name: str, proc: subprocess.Popen):
        super().__init__()
        self.instance_name = instance_name
        self.proc = proc

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"Running: {self.instance_name}", id="title-bar")
        yield Log(id="console-log", max_lines=5000)
        yield Static("[esc] Close logs", id="status-bar")
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
        except Exception as e:  # noqa: BLE001
            emit(f"[VKL] output stream ended: {e}")
        emit(f"[VKL] process exited with code {self.proc.poll()}")

    def action_close(self) -> None:
        self.app.pop_screen()

class MainScreen(Screen):
    BINDINGS = [
        Binding("n", "new_instance", "New"),
        Binding("enter", "launch_instance", "Launch"),
        Binding("d", "delete_instance", "Delete"),
        Binding("r", "rename_instance", "Rename"),
        Binding("a", "accounts", "Accounts"),
        Binding("p", "modrinth", "Modrinth"),
        Binding("s", "settings", "Settings"),
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
                yield Static(
                    "[n] New instance\n"
                    "[enter] Launch\n"
                    "[d] Delete\n"
                    "[r] Rename\n"
                    "[a] Accounts\n"
                    "[p] Browse Modrinth packs\n"
                    "[s] Settings\n"
                    "[q] Quit",
                )
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#instance-table", DataTable)
        table.add_columns("Name", "Version", "Loader", "Last played")
        table.cursor_type = "row"
        self.refresh_list()

    def on_screen_resume(self) -> None:
        self.refresh_list()

    def refresh_list(self) -> None:
        table = self.query_one("#instance-table", DataTable)
        table.clear()
        self._instances = instances.list_instances()
        for inst in sorted(self._instances, key=lambda i: -i.last_played):
            table.add_row(inst.name, inst.mc_version, inst.loader, fmt_ts(inst.last_played), key=inst.name)

        acc = auth.get_active_account()
        label = f"{acc.username} ({acc.kind})" if acc else "(none — press [a] to add one)"
        self.query_one("#active-account", Static).update(label)

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

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_launch_instance()

    def action_launch_instance(self) -> None:
        inst = self._selected_instance()
        if not inst:
            self.app.push_screen(MessageModal("No instance", "Select Minecraft instance first.", is_error=True))
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
                account = auth.ensure_fresh(account)
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
