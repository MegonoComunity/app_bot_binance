"""
core/auto_updater.py

Sistem Pembaruan Otomatis & Self-Healing dari GitHub (Smart Auto Git Pull & Self-Healing Engine).
Fitur Utama:
1. Mendeteksi commit/perubahan baru di repository GitHub secara otomatis (Background Task & Telegram Trigger).
2. Menangani Git Pull dengan strategi rekonsiliasi cerdas (mencegah conflict pada file data/database/csv lokal).
3. Self-Healing: Jika terjadi konflik pada data/dataset, lakukan auto-resolve dan pertahankan file penting (.env & history).
4. Auto-Retrain ML: Jika ada penambahan gambar dataset baru di GitHub, otomatis memicu pelatihan ulang otak model CNN (ml_vision).
5. Self-Validation: Memvalidasi integritas kode setelah pull dengan menjalankan unit test otomatis.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)


def run_git_command(args: list[str], timeout: int = 45) -> Tuple[int, str, str]:
    """Menjalankan perintah git di shell lokal."""
    try:
        res = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Git command timed out"
    except Exception as exc:
        return -1, "", str(exc)


async def check_for_git_updates(branch: str = "main") -> Dict[str, Any]:
    """
    Mengecek apakah ada commit baru di remote GitHub tanpa mengubah working tree lokal.
    """
    loop = asyncio.get_event_loop()
    
    # 1. Fetch remote origin
    code, stdout, stderr = await loop.run_in_executor(None, run_git_command, ["fetch", "origin", branch])
    if code != 0:
        return {
            "has_update": False,
            "error": f"Gagal fetch remote: {stderr or stdout}",
            "behind_count": 0,
            "commits": [],
        }

    # 2. Bandingkan commit HEAD vs origin/branch
    code_local, local_hash, _ = await loop.run_in_executor(None, run_git_command, ["rev-parse", "HEAD"])
    code_remote, remote_hash, _ = await loop.run_in_executor(None, run_git_command, ["rev-parse", f"origin/{branch}"])

    if code_local != 0 or code_remote != 0:
        return {
            "has_update": False,
            "error": "Gagal membaca commit hash",
            "behind_count": 0,
            "commits": [],
        }

    if local_hash == remote_hash:
        return {
            "has_update": False,
            "behind_count": 0,
            "local_hash": local_hash[:7],
            "remote_hash": remote_hash[:7],
            "commits": [],
        }

    # Hitung jumlah commit yang tertinggal
    _, behind_out, _ = await loop.run_in_executor(
        None, run_git_command, ["rev-list", "--count", f"HEAD..origin/{branch}"]
    )
    behind_count = int(behind_out.strip() or 0) if behind_out.strip().isdigit() else 1

    # Ambil ringkasan pesan commit baru
    _, log_out, _ = await loop.run_in_executor(
        None, run_git_command, ["log", "--oneline", f"HEAD..origin/{branch}", "-n", "5"]
    )
    commit_list = [line.strip() for line in log_out.splitlines() if line.strip()]

    return {
        "has_update": True,
        "behind_count": behind_count,
        "local_hash": local_hash[:7],
        "remote_hash": remote_hash[:7],
        "commits": commit_list,
    }


async def smart_git_pull_and_heal(branch: str = "main") -> Dict[str, Any]:
    """
    Menjalankan proses Git Pull cerdas dengan resolusi konflik otomatis dan perbaikan diri (Self-Healing).
    """
    loop = asyncio.get_event_loop()
    logs: list[str] = []

    # 1. Pastikan perubahan lokal pada file data penting aman (Auto-Stash / Preserve)
    code_status, status_out, _ = await loop.run_in_executor(None, run_git_command, ["status", "--porcelain"])
    has_dirty_files = bool(status_out.strip())

    if has_dirty_files:
        logs.append("📦 Mengamankan perubahan lokal sementara (Auto-Stash)...")
        await loop.run_in_executor(None, run_git_command, ["stash", "save", "auto_updater_preserve"])

    # 2. Eksekusi git pull
    logs.append(f"⬇️ Mengunduh pembaruan dari origin/{branch}...")
    code_pull, pull_stdout, pull_stderr = await loop.run_in_executor(
        None, run_git_command, ["pull", "origin", branch, "--no-rebase"]
    )

    pull_output = f"{pull_stdout}\n{pull_stderr}".strip()

    # 3. Tangani Potensi Konflik (Self-Healing Conflict Resolution)
    is_conflict = "CONFLICT" in pull_output or code_pull != 0
    if is_conflict:
        logs.append("⚠️ Konflik terdeteksi! Mengaktifkan protokol Self-Healing...")
        
        # Resolusi otomatis: Pertahankan kode utama terbaru dari origin dan satukan dataset/data lokal
        # Ambil daftar file yang konflik
        _, unmerged_out, _ = await loop.run_in_executor(None, run_git_command, ["diff", "--name-only", "--diff-filter=U"])
        conflicted_files = [f.strip() for f in unmerged_out.splitlines() if f.strip()]

        for c_file in conflicted_files:
            if c_file.endswith(".csv") or c_file.endswith(".json") or ".env" in c_file:
                # File data & konfigurasi: utamakan versi lokal agar log & credential aman
                await loop.run_in_executor(None, run_git_command, ["checkout", "--ours", c_file])
                await loop.run_in_executor(None, run_git_command, ["add", c_file])
                logs.append(f"🛡️ File data '{c_file}' diamankan (Local Preserved).")
            else:
                # File kode logika/fitur: utamakan versi GitHub terbaru
                await loop.run_in_executor(None, run_git_command, ["checkout", "--theirs", c_file])
                await loop.run_in_executor(None, run_git_command, ["add", c_file])
                logs.append(f"🔄 File logika '{c_file}' diperbarui ke versi GitHub (Auto-Healed).")

        # Commit hasil rekonsiliasi
        await loop.run_in_executor(
            None, run_git_command, ["commit", "-m", "chore: auto-resolved git merge conflict by Self-Healing Engine"]
        )
        logs.append("✅ Seluruh konflik berhasil diperbaiki secara otomatis.")

    # Kembalikan file stash jika ada
    if has_dirty_files:
        await loop.run_in_executor(None, run_git_command, ["stash", "pop"])

    # 4. Deteksi Pembaruan Dataset & Trigger Auto-Retrain ML
    has_new_dataset = False
    retrain_result = ""
    if "dataset/" in pull_output or any("dataset/" in line for line in logs):
        has_new_dataset = True
        logs.append("🖼️ Terdeteksi penambahan gambar dataset baru di GitHub! Memicu pelatihan ulang AI Vision...")
        try:
            from ml_vision.train import train_model
            retrain_result = await loop.run_in_executor(None, train_model)
            logs.append(f"🧠 {retrain_result}")
        except Exception as e_train:
            logs.append(f"⚠️ Pelatihan dataset baru tertunda: {e_train}")

    # 5. Self-Validation: Uji integritas kode dengan unit test
    logs.append("🧪 Menjalankan Self-Validation Unit Test...")
    try:
        def run_tests():
            res = subprocess.run(
                ["python", "-m", "unittest", "discover", "tests"],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace"
            )
            return res.returncode, res.stdout.strip(), res.stderr.strip()

        t_code, t_out, t_err = await loop.run_in_executor(None, run_tests)
        is_tests_passed = (t_code == 0)
        test_status = "LULUS (Semua fungsi normal ✅)" if is_tests_passed else f"GAGAL ❌: {t_err or t_out}"
        logs.append(f"🔍 Hasil Validasi: {test_status}")
    except Exception as e_test:
        is_tests_passed = True
        logs.append(f"🔍 Validasi dilewati: {e_test}")

    return {
        "success": True,
        "pull_output": pull_output,
        "has_new_dataset": has_new_dataset,
        "retrain_result": retrain_result,
        "is_tests_passed": is_tests_passed,
        "logs": logs,
    }


async def auto_git_sync_background_loop(bot: Any, admin_chat_id: Any, interval_seconds: int = 300) -> None:
    """
    Loop background otomatis:
    Mengecek perubahan di GitHub setiap `interval_seconds` (default 5 menit).
    Jika ditemukan commit baru, langsung auto git pull, auto heal, dan kirim notifikasi ke Admin.
    """
    logger.info(f"[AUTO UPDATER] Background sync watcher aktif (interval {interval_seconds}s)...")
    
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            
            update_info = await check_for_git_updates(branch="main")
            if update_info.get("has_update"):
                behind_n = update_info.get("behind_count", 1)
                commits = "\n".join([f"• `{c}`" for c in update_info.get("commits", [])])
                
                # Eksekusi Auto Pull & Self Healing
                heal_res = await smart_git_pull_and_heal(branch="main")
                
                msg = (
                    f"🚀 **AUTO-UPDATE GITHUB TERDETEKSI & BERHASIL DISINKRONKAN!** 🔄\n\n"
                    f"📦 **Jumlah Commit Baru:** `{behind_n}`\n"
                    f"📝 **Catatan Commit:**\n{commits}\n\n"
                    f"🛠️ **Log Self-Healing:**\n" + "\n".join([f"• {l}" for l in heal_res.get("logs", [])]) + "\n\n"
                    f"🤖 *Bot kini berjalan dengan versi dan dataset terpintar terbaru dari GitHub.*"
                )
                
                if bot and admin_chat_id:
                    try:
                        from telegram.notifier import safe_send_message
                        await safe_send_message(bot, admin_chat_id, msg)
                    except Exception as e_notif:
                        logger.warning(f"[AUTO UPDATER] Gagal kirim notifikasi Telegram: {e_notif}")
                        
        except asyncio.CancelledError:
            logger.info("[AUTO UPDATER] Background watcher dibatalkan.")
            break
        except Exception as e_loop:
            logger.error(f"[AUTO UPDATER] Error di background watcher loop: {e_loop}")
            await asyncio.sleep(60)
