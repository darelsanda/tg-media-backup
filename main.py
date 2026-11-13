import asyncio
import json
import os
import re
import flet as ft
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telethon.tl.types import MessageMediaPoll, MessageMediaWebPage

MAX_RETRY = 5


def get_base_dir():
    # Coba simpan ke folder Download publik agar mudah dibuka di Galeri/File Manager
    android_dl = "/storage/emulated/0/Download/telegram_media"
    if os.path.exists("/storage/emulated/0/Download"):
        try:
            os.makedirs(android_dl, exist_ok=True)
            test_file = os.path.join(android_dl, ".write_test")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
            return android_dl
        except Exception:
            # Jika Android 11+ memblokir akses ke Download, otomatis alihkan ke folder aman aplikasi
            pass

    # Jalur cadangan resmi Android yang 100% pasti diizinkan tanpa izin khusus
    ext_storage = os.getenv("FLET_APP_STORAGE_DATA")
    if ext_storage:
        return os.path.join(ext_storage, "telegram_media")
    return "telegram_media"


def safe_name(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "_", text).strip().rstrip(".")
    return text[:150] or "tanpa_nama"


def media_kind(msg) -> str:
    if msg.photo:
        return "photo"
    if msg.video_note:
        return "video_note"
    if msg.video or msg.gif:
        return "video"
    if msg.voice:
        return "voice"
    if msg.audio:
        return "audio"
    if msg.sticker:
        return "sticker"
    return "file"


def build_filename(msg) -> str:
    ext = msg.file.ext if msg.file and msg.file.ext else ""
    if msg.file and msg.file.name:
        name = safe_name(msg.file.name)
        if ext and not name.lower().endswith(ext.lower()):
            name += ext
        return name
    stamp = msg.date.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{media_kind(msg)}_{stamp}{ext}"


def unique_path(folder: str, filename: str) -> str:
    path = os.path.join(folder, filename)
    if not os.path.exists(path) and not os.path.exists(path + ".part"):
        return path
    stem, ext = os.path.splitext(filename)
    n = 1
    while True:
        path = os.path.join(folder, f"{stem} ({n}){ext}")
        if not os.path.exists(path) and not os.path.exists(path + ".part"):
            return path
        n += 1


def load_progress(folder: str) -> set:
    p = os.path.join(folder, ".progress.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_progress(folder: str, done: set):
    p = os.path.join(folder, ".progress.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f)
    os.replace(tmp, p)


async def main(page: ft.Page):
    page.title = "TG Media Backup"
    page.theme_mode = ft.ThemeMode.DARK
    page.scroll = ft.ScrollMode.AUTO
    page.padding = 16

    app_data = os.getenv("FLET_APP_STORAGE_DATA", ".")
    os.makedirs(app_data, exist_ok=True)
    config_path = os.path.join(app_data, "config.json")
    session_path = os.path.join(app_data, "sesi_download")

    state = {
        "client": None,
        "stop": False,
        "dialogs": [],
        "downloaded_session": 0,
        "failed_session": 0,
    }

    def load_config():
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_config(api_id: int, api_hash: str):
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"api_id": api_id, "api_hash": api_hash}, f)

    # --- UI Header ---
    logout_btn = ft.IconButton(
        icon=ft.Icons.LOGOUT,
        tooltip="Ganti Akun / API",
        visible=False,
    )
    header = ft.Row(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.CLOUD_DOWNLOAD, color=ft.Colors.CYAN_400, size=28),
                    ft.Text("TG Media Backup", size=20, weight=ft.FontWeight.BOLD),
                ]
            ),
            logout_btn,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    status_banner = ft.Container(
        content=ft.Text("Memeriksa sesi tersimpan...", color=ft.Colors.CYAN_200, size=13),
        bgcolor=ft.Colors.BLUE_GREY_900,
        padding=10,
        border_radius=8,
    )

    # --- UI Login & API Setup ---
    api_id_input = ft.TextField(label="API ID (angka dari my.telegram.org)", keyboard_type=ft.KeyboardType.NUMBER)
    api_hash_input = ft.TextField(label="API Hash (dari my.telegram.org)", password=True, can_reveal_password=True)
    phone_input = ft.TextField(label="Nomor HP Telegram (+628...)", keyboard_type=ft.KeyboardType.PHONE)
    otp_input = ft.TextField(label="Kode OTP Telegram", visible=False, keyboard_type=ft.KeyboardType.NUMBER)
    pass_input = ft.TextField(label="Password 2FA (jika aktif)", password=True, visible=False)
    login_btn = ft.ElevatedButton("Hubungkan & Kirim OTP", icon=ft.Icons.LOGIN, width=300)

    login_card = ft.Container(
        content=ft.Column(
            [
                ft.Text("Konfigurasi Akun Telegram", size=16, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Dapatkan API ID & Hash gratis di my.telegram.org. Data disimpan aman hanya di HP Anda.",
                    size=12,
                    color=ft.Colors.GREY_400,
                ),
                api_id_input,
                api_hash_input,
                phone_input,
                otp_input,
                pass_input,
                ft.Container(content=login_btn, alignment=ft.alignment.center, padding=ft.padding.only(top=8)),
            ],
            spacing=10,
        ),
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        padding=16,
        border_radius=12,
        visible=False,
    )

    # --- UI Dashboard ---
    stat_groups = ft.Text("0", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.CYAN_400)
    stat_done = ft.Text("0", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_400)
    stat_fail = ft.Text("0", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.AMBER_400)

    def make_stat_card(title: str, value_text: ft.Text):
        return ft.Container(
            content=ft.Column(
                [value_text, ft.Text(title, size=11, color=ft.Colors.GREY_400)],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=2,
            ),
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            padding=12,
            border_radius=10,
            expand=True,
        )

    dashboard_stats = ft.Row(
        [
            make_stat_card("Grup Dipilih", stat_groups),
            make_stat_card("Terunduh", stat_done),
            make_stat_card("Gagal/Skip", stat_fail),
        ],
        spacing=8,
    )

    progress_bar = ft.ProgressBar(visible=False, color=ft.Colors.CYAN_400)
    current_file_text = ft.Text("Menunggu perintah...", size=12, color=ft.Colors.GREY_300)

    groups_col = ft.Column(spacing=4)
    log_view = ft.ListView(height=180, spacing=3, auto_scroll=True)

    def add_log(msg: str):
        log_view.controls.append(ft.Text(msg, size=11, font_family="monospace"))
        if len(log_view.controls) > 120:
            log_view.controls.pop(0)
        page.update()

    def update_selected_count(e=None):
        selected_count = sum(1 for cb in groups_col.controls if cb.value)
        stat_groups.value = str(selected_count)
        page.update()

    def select_all_groups(val: bool):
        for cb in groups_col.controls:
            cb.value = val
        update_selected_count()

    select_all_btn = ft.TextButton("Pilih Semua", on_click=lambda e: select_all_groups(True))
    clear_all_btn = ft.TextButton("Reset", on_click=lambda e: select_all_groups(False))

    start_btn = ft.ElevatedButton(
        "Mulai Backup",
        icon=ft.Icons.DOWNLOAD,
        bgcolor=ft.Colors.GREEN_700,
        color=ft.Colors.WHITE,
        expand=True,
    )
    stop_btn = ft.ElevatedButton(
        "Jeda / Stop",
        icon=ft.Icons.STOP,
        bgcolor=ft.Colors.RED_700,
        color=ft.Colors.WHITE,
        disabled=True,
        expand=True,
    )

    dashboard_box = ft.Column(
        [
            dashboard_stats,
            ft.Container(
                content=ft.Column([current_file_text, progress_bar], spacing=6),
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                padding=12,
                border_radius=10,
            ),
            ft.Row(
                [
                    ft.Text("Daftar Grup & Channel", weight=ft.FontWeight.BOLD, size=15),
                    ft.Row([select_all_btn, clear_all_btn], spacing=0),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Container(
                content=groups_col,
                height=210,
                border=ft.border.all(1, ft.Colors.OUTLINE),
                padding=8,
                border_radius=10,
            ),
            ft.Row([start_btn, stop_btn], spacing=10),
            ft.Text("Log Aktivitas", weight=ft.FontWeight.BOLD, size=14),
            ft.Container(
                content=log_view,
                border=ft.border.all(1, ft.Colors.OUTLINE),
                padding=8,
                border_radius=10,
                bgcolor=ft.Colors.BLACK26,
            ),
        ],
        spacing=12,
        visible=False,
    )

    page.add(header, status_banner, login_card, dashboard_box)

    def create_client(api_id: int, api_hash: str):
        return TelegramClient(
            session_path,
            api_id,
            api_hash,
            connection_retries=None,
            retry_delay=5,
            auto_reconnect=True,
            request_retries=15,
            timeout=30,
        )

    async def load_dialogs():
        status_banner.content.value = "Memuat daftar grup & channel..."
        page.update()
        client = state["client"]
        dialogs = [d async for d in client.iter_dialogs() if d.is_group or d.is_channel]
        state["dialogs"] = dialogs
        groups_col.controls.clear()
        for idx, d in enumerate(dialogs):
            groups_col.controls.append(
                ft.Checkbox(label=d.name, value=False, data=idx, on_change=update_selected_count)
            )
        login_card.visible = False
        dashboard_box.visible = True
        logout_btn.visible = True
        status_banner.content.value = f"Terhubung • Tersedia {len(dialogs)} grup/channel"
        page.update()

    async def on_login_click(e):
        try:
            raw_id = api_id_input.value.strip()
            raw_hash = api_hash_input.value.strip()
            phone = phone_input.value.strip()

            if not raw_id.isdigit() or not raw_hash or not phone:
                status_banner.content.value = "Isi API ID (angka), API Hash, dan Nomor HP dengan lengkap!"
                page.update()
                return

            api_id = int(raw_id)
            if state["client"] is None:
                state["client"] = create_client(api_id, raw_hash)
                await state["client"].connect()

            client = state["client"]
            if not otp_input.visible:
                await client.send_code_request(phone)
                save_config(api_id, raw_hash)
                otp_input.visible = True
                api_id_input.disabled = True
                api_hash_input.disabled = True
                phone_input.disabled = True
                login_btn.text = "Verifikasi OTP"
                status_banner.content.value = "Kode OTP terkirim ke aplikasi Telegram Anda."
            else:
                try:
                    await client.sign_in(phone, otp_input.value.strip())
                except SessionPasswordNeededError:
                    if not pass_input.visible:
                        pass_input.visible = True
                        status_banner.content.value = "Akun memakai 2FA. Masukkan password Anda."
                        page.update()
                        return
                    await client.sign_in(password=pass_input.value.strip())
                await load_dialogs()
        except Exception as err:
            status_banner.content.value = f"Error: {err}"
        page.update()

    async def on_logout_click(e):
        state["stop"] = True
        if state["client"]:
            try:
                await state["client"].log_out()
            except Exception:
                await state["client"].disconnect()
            state["client"] = None
        if os.path.exists(config_path):
            os.remove(config_path)
        api_id_input.disabled = False
        api_hash_input.disabled = False
        phone_input.disabled = False
        otp_input.visible = False
        pass_input.visible = False
        login_btn.text = "Hubungkan & Kirim OTP"
        dashboard_box.visible = False
        logout_btn.visible = False
        login_card.visible = True
        status_banner.content.value = "Sesi dihapus. Silakan masukkan kredensial baru."
        page.update()

    login_btn.on_click = on_login_click
    logout_btn.on_click = on_logout_click

    async def download_one(msg, final_path) -> bool:
        part = final_path + ".part"
        client = state["client"]
        for attempt in range(1, MAX_RETRY + 1):
            if state["stop"]:
                return False
            try:
                await client.download_media(msg, file=part)
                if os.path.exists(part):
                    os.replace(part, final_path)
                    return True
                return False
            except FloodWaitError as e:
                add_log(f"FloodWait: tunggu {e.seconds}s...")
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                if os.path.exists(part):
                    try:
                        os.remove(part)
                    except OSError:
                        pass
                wait = min(10 * attempt, 60)
                add_log(f"Retry {attempt}/{MAX_RETRY} ({e.__class__.__name__})...")
                await asyncio.sleep(wait)
        return False

    async def on_start_click(e):
        selected = [state["dialogs"][cb.data] for cb in groups_col.controls if cb.value]
        if not selected:
            status_banner.content.value = "Centang minimal 1 grup terlebih dahulu!"
            page.update()
            return

        state["stop"] = False
        state["downloaded_session"] = 0
        state["failed_session"] = 0
        stat_done.value = "0"
        stat_fail.value = "0"
        start_btn.disabled = True
        stop_btn.disabled = False
        progress_bar.visible = True

        base_dir = get_base_dir()
        os.makedirs(base_dir, exist_ok=True)
        add_log(f"Folder simpan: {base_dir}")

        client = state["client"]
        for g_idx, dialog in enumerate(selected, 1):
            if state["stop"]:
                break
            folder = os.path.join(base_dir, safe_name(dialog.name))
            os.makedirs(folder, exist_ok=True)
            status_banner.content.value = f"Memproses Grup {g_idx}/{len(selected)}: {dialog.name}"
            add_log(f"=== [{g_idx}/{len(selected)}] {dialog.name} ===")

            for fn in os.listdir(folder):
                if fn.endswith(".part"):
                    try:
                        os.remove(os.path.join(folder, fn))
                    except OSError:
                        pass

            done = load_progress(folder)
            add_log(f"Riwayat tersimpan: {len(done)} file")
            count = 0

            async for msg in client.iter_messages(dialog.entity, reverse=True):
                if state["stop"]:
                    add_log("Proses dijeda oleh pengguna.")
                    break
                if not msg.media or isinstance(msg.media, (MessageMediaWebPage, MessageMediaPoll)):
                    continue
                if msg.id in done:
                    continue

                filename = build_filename(msg)
                target = unique_path(folder, filename)
                current_file_text.value = f"Mengunduh: {filename}"
                page.update()

                if await download_one(msg, target):
                    done.add(msg.id)
                    save_progress(folder, done)
                    count += 1
                    state["downloaded_session"] += 1
                    stat_done.value = str(state["downloaded_session"])
                    add_log(f"[{count}] {os.path.basename(target)}")
                    await asyncio.sleep(0.5)
                else:
                    if not state["stop"]:
                        state["failed_session"] += 1
                        stat_fail.value = str(state["failed_session"])
                        add_log(f"Gagal/Skip pesan {msg.id}")

            add_log(f"Selesai {dialog.name}: +{count} file baru")

        current_file_text.value = "Selesai / Dijeda. Jalankan ulang kapan saja untuk melanjutkan."
        status_banner.content.value = "Status: Siap"
        progress_bar.visible = False
        start_btn.disabled = False
        stop_btn.disabled = True
        page.update()

    def on_stop_click(e):
        state["stop"] = True
        current_file_text.value = "Menghentikan setelah file aktif selesai..."
        page.update()

    start_btn.on_click = on_start_click
    stop_btn.on_click = on_stop_click

    # --- Cek Konfigurasi Tersimpan saat Aplikasi Dibuka ---
    cfg = load_config()
    if cfg.get("api_id") and cfg.get("api_hash"):
        try:
            state["client"] = create_client(int(cfg["api_id"]), cfg["api_hash"])
            await state["client"].connect()
            if await state["client"].is_user_authorized():
                await load_dialogs()
                return
        except Exception:
            pass

    status_banner.content.value = "Masukkan API ID, API Hash, dan Nomor HP Anda."
    login_card.visible = True
    page.update()


ft.app(target=main)