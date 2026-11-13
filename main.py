import asyncio
import json
import os
import re
import flet as ft
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

def get_base_dir():
    for path in ["/storage/emulated/0/Download/Telegram_Backup", "/sdcard/Download/Telegram_Backup"]:
        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, ".test")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
            return path
        except Exception:
            continue
    fallback = os.path.join(os.path.expanduser("~"), "Telegram_Backup")
    os.makedirs(fallback, exist_ok=True)
    return fallback

BASE_DIR = get_base_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
SESSION_FILE = os.path.join(BASE_DIR, "tg_backup_session")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"api_id": "", "api_hash": "", "phone": ""}

def save_config(data):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

def clean_name(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name or "Unknown").strip()

async def main(page: ft.Page):
    page.title = "TG Media Backup"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 16
    page.scroll = ft.ScrollMode.AUTO

    cfg = load_config()
    client_holder = {"client": None, "dialogs": [], "checkboxes": {}, "is_running": False}

    # --- UI Kredensial ---
    api_id_input = ft.TextField(label="API ID", value=str(cfg.get("api_id", "")), keyboard_type=ft.KeyboardType.NUMBER)
    api_hash_input = ft.TextField(label="API Hash", value=cfg.get("api_hash", ""), password=True, can_reveal_password=True)
    phone_input = ft.TextField(label="Nomor HP (+62...)", value=cfg.get("phone", ""), keyboard_type=ft.KeyboardType.PHONE)
    otp_input = ft.TextField(label="Kode OTP Telegram", visible=False, keyboard_type=ft.KeyboardType.NUMBER)
    pwd_input = ft.TextField(label="Password 2FA (jika ada)", visible=False, password=True, can_reveal_password=True)

    status_text = ft.Text("Status: Belum terhubung", color=ft.Colors.AMBER_300, size=13)
    progress_bar = ft.ProgressBar(value=0, visible=False)
    stats_text = ft.Text("", size=12, color=ft.Colors.CYAN_200)

    # --- Daftar Grup yang Bisa Dipilih & Di-scroll ---
    group_list_view = ft.ListView(
        expand=False,
        height=280,
        spacing=4,
        padding=8,
        auto_scroll=False,
    )

    search_group_input = ft.TextField(
        label="Cari nama grup / channel...",
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        visible=False,
    )

    def render_group_list(filter_text=""):
        group_list_view.controls.clear()
        keyword = filter_text.lower().strip()
        for d in client_holder["dialogs"]:
            title = d.name or "Tanpa Nama"
            if keyword and keyword not in title.lower():
                continue
            label_type = "Channel" if (d.is_channel and not d.is_group) else "Grup"
            cb = client_holder["checkboxes"].get(d.id)
            if cb is None:
                cb = ft.Checkbox(label=f"[{label_type}] {title}", value=False)
                client_holder["checkboxes"][d.id] = cb
            group_list_view.controls.append(cb)
        page.update()

    search_group_input.on_change = lambda e: render_group_list(e.control.value)

    def select_all_groups(state: bool):
        for cb in group_list_view.controls:
            if isinstance(cb, ft.Checkbox):
                cb.value = state
        page.update()

    btn_select_all = ft.TextButton("Pilih Semua", on_click=lambda _: select_all_groups(True), visible=False)
    btn_deselect_all = ft.TextButton("Batal Pilih", on_click=lambda _: select_all_groups(False), visible=False)

    group_container = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Text("Daftar Grup & Channel", weight=ft.FontWeight.BOLD, size=15),
                ft.Row([btn_select_all, btn_deselect_all], spacing=0),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            search_group_input,
            ft.Container(
                content=group_list_view,
                border=ft.border.all(1, ft.Colors.OUTLINE),
                border_radius=8,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            ),
        ]),
        visible=False,
    )

    async def load_all_dialogs(client: TelegramClient):
        status_text.value = "Status: Memuat seluruh grup & channel..."
        page.update()
        dialogs = []
        async for d in client.iter_dialogs(limit=None, archived=None):
            if d.is_group or d.is_channel:
                dialogs.append(d)
        client_holder["dialogs"] = dialogs
        client_holder["checkboxes"] = {}
        search_group_input.visible = True
        btn_select_all.visible = True
        btn_deselect_all.visible = True
        group_container.visible = True
        btn_start.visible = True
        render_group_list()
        status_text.value = f"Status: Terhubung ({len(dialogs)} grup/channel ditemukan)"
        status_text.color = ft.Colors.GREEN_400
        page.update()

    async def handle_connect(e):
        try:
            api_id = int(api_id_input.value.strip())
            api_hash = api_hash_input.value.strip()
            phone = phone_input.value.strip()
        except ValueError:
            status_text.value = "Error: API ID harus berupa angka!"
            status_text.color = ft.Colors.RED_400
            page.update()
            return

        save_config({"api_id": api_id, "api_hash": api_hash, "phone": phone})
        status_text.value = "Status: Menghubungkan ke Telegram..."
        page.update()

        client = client_holder["client"]
        if client is None:
            client = TelegramClient(SESSION_FILE, api_id, api_hash)
            client_holder["client"] = client

        await client.connect()

        if not await client.is_user_authorized():
            if not otp_input.visible:
                await client.send_code_request(phone)
                otp_input.visible = True
                status_text.value = "Status: Kode OTP terkirim ke Telegram Anda. Masukkan lalu klik Hubungkan lagi."
                status_text.color = ft.Colors.AMBER_300
                page.update()
                return
            else:
                try:
                    await client.sign_in(phone, otp_input.value.strip())
                except SessionPasswordNeededError:
                    if not pwd_input.visible:
                        pwd_input.visible = True
                        status_text.value = "Status: Akun memakai 2FA. Masukkan password lalu klik Hubungkan."
                        page.update()
                        return
                    await client.sign_in(password=pwd_input.value.strip())

        otp_input.visible = False
        pwd_input.visible = False
        await load_all_dialogs(client)

    async def handle_download(e):
        if client_holder["is_running"]:
            client_holder["is_running"] = False
            btn_start.text = "Mulai Backup"
            status_text.value = "Status: Menghentikan unduhan..."
            page.update()
            return

        selected_dialogs = [
            d for d in client_holder["dialogs"]
            if client_holder["checkboxes"].get(d.id) and client_holder["checkboxes"][d.id].value
        ]

        if not selected_dialogs:
            status_text.value = "Pilih minimal 1 grup terlebih dahulu!"
            status_text.color = ft.Colors.RED_400
            page.update()
            return

        client_holder["is_running"] = True
        btn_start.text = "Hentikan Backup"
        btn_start.bgcolor = ft.Colors.RED_700
        progress_bar.visible = True
        total_downloaded = 0

        client: TelegramClient = client_holder["client"]

        for d in selected_dialogs:
            if not client_holder["is_running"]:
                break
            folder_name = clean_name(d.name)
            target_dir = os.path.join(BASE_DIR, folder_name)
            os.makedirs(target_dir, exist_ok=True)

            status_text.value = f"Memindai: {d.name}..."
            status_text.color = ft.Colors.CYAN_300
            page.update()

            async for msg in client.iter_messages(d.entity, reverse=True):
                if not client_holder["is_running"]:
                    break
                if msg.media and (msg.photo or msg.video or msg.document):
                    try:
                        file_path = await msg.download_media(file=target_dir)
                        if file_path:
                            total_downloaded += 1
                            stats_text.value = f"Tersimpan: {total_downloaded} file | Folder: {folder_name}"
                            page.update()
                    except Exception:
                        continue

        client_holder["is_running"] = False
        btn_start.text = "Mulai Backup"
        btn_start.bgcolor = ft.Colors.BLUE_700
        progress_bar.visible = False
        status_text.value = f"Selesai! Total {total_downloaded} media disimpan di {BASE_DIR}"
        status_text.color = ft.Colors.GREEN_400
        page.update()

    btn_connect = ft.ElevatedButton("Hubungkan & Muat Grup", icon=ft.Icons.LOGIN, on_click=handle_connect)
    btn_start = ft.ElevatedButton("Mulai Backup", icon=ft.Icons.DOWNLOAD, on_click=handle_download, visible=False)

    page.add(
        ft.Text("TG Media Backup", size=22, weight=ft.FontWeight.BOLD),
        ft.Text(f"Lokasi simpan: {BASE_DIR}", size=11, color=ft.Colors.GREY_400),
        api_id_input,
        api_hash_input,
        phone_input,
        otp_input,
        pwd_input,
        btn_connect,
        status_text,
        group_container,
        btn_start,
        progress_bar,
        stats_text,
    )

ft.app(target=main)
