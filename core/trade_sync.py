"""
core/trade_sync.py

Modul sinkronisasi menyeluruh untuk akun REAL Exchange (Bitunix / Binance).
Fitur:
1. Sinkronisasi Saldo Real (Total Wallet Balance, Available Balance, Unrealized PnL, Margin Locked, Frozen).
2. Sinkronisasi Posisi Terbuka (Open Positions) dari Exchange API dan auto-restore ke bot_state["active_trade_meta"]
   sehingga bot langsung mengenali dan memantau posisi aktif saat bot baru dinyalakan / pindah server.
3. Sinkronisasi Riwayat Trade Closed Positions dari Exchange API langsung ke PostgreSQL (trade_history).
4. Auto-update bot_state dan scanner log.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

from config.settings import bot_config
from core.exchanges.base import BaseExchange
from core.exchanges.factory import get_exchange_adapter

logger = logging.getLogger(__name__)


async def sync_real_exchange_account(
    client: Optional[Any] = None,
    sync_history: bool = True,
    history_limit: int = 50,
    bot_state_ref: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Sinkronisasi menyeluruh akun real dari Exchange API (Bitunix / Binance).
    Mengembalikan dictionary status, saldo, posisi aktif, dan histori yang tersinkronisasi.
    """
    # 1. Dapatkan client instance & bot_state reference
    if bot_state_ref is None:
        try:
            from telegram.bot_handler import bot_state
            bot_state_ref = bot_state
        except ImportError:
            bot_state_ref = {}

    if client is None:
        client = bot_state_ref.get("client") if bot_state_ref is not None else None
        if client is None:
            active_ex = getattr(bot_config, "active_exchange", "BITUNIX")
            client = get_exchange_adapter(active_ex, is_testnet=False)
            await client.init()
            if bot_state_ref is not None:
                bot_state_ref["client"] = client

    ex_name = getattr(client, "exchange_name", getattr(bot_config, "active_exchange", "BITUNIX")).upper()

    result: Dict[str, Any] = {
        "success": False,
        "exchange": ex_name,
        "mode": "REAL",
        "total_wallet_balance": 0.0,
        "available_balance": 0.0,
        "unrealized_pnl": 0.0,
        "margin_locked": 0.0,
        "frozen": 0.0,
        "open_positions_count": 0,
        "open_positions": [],
        "synced_history_count": 0,
        "synced_trades": [],
        "error": None,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 2. Ambil Saldo Real dari API Exchange
    try:
        if isinstance(client, BaseExchange):
            bal_info = await client.get_account_balance()
            total_bal = float(bal_info.get("total_wallet_balance", 0.0))
            avail_bal = float(bal_info.get("available_balance", total_bal))
            unreal_pnl = float(bal_info.get("unrealized_pnl", 0.0))
            margin_locked = float(bal_info.get("margin_locked", 0.0))
            frozen = float(bal_info.get("frozen", 0.0))
        elif hasattr(client, "futures_account"):
            acc_info = await client.futures_account()
            total_bal = float(acc_info.get("totalMarginBalance", 0.0))
            avail_bal = float(acc_info.get("availableBalance", total_bal))
            unreal_pnl = float(acc_info.get("totalUnrealizedProfit", 0.0))
            margin_locked = float(acc_info.get("totalInitialMargin", 0.0))
            frozen = 0.0
        else:
            total_bal = 0.0
            avail_bal = 0.0
            unreal_pnl = 0.0
            margin_locked = 0.0
            frozen = 0.0

        result["total_wallet_balance"] = total_bal
        result["available_balance"] = avail_bal
        result["unrealized_pnl"] = unreal_pnl
        result["margin_locked"] = margin_locked
        result["frozen"] = frozen
        result["success"] = True

        if bot_state_ref is not None:
            bot_state_ref["account_balance"] = {
                "total_wallet_balance": total_bal,
                "available_balance": avail_bal,
                "unrealized_pnl": unreal_pnl,
                "margin_locked": margin_locked,
                "frozen": frozen,
                "exchange": ex_name,
                "mode": "REAL",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
    except Exception as e_bal:
        err_msg = str(e_bal)
        logger.error(f"[REAL SYNC] Gagal fetch real balance {ex_name}: {err_msg}")
        result["error"] = err_msg

    # 3. Ambil dan Pulihkan Posisi Terbuka Real (Open Positions)
    try:
        raw_positions = []
        if isinstance(client, BaseExchange) and hasattr(client, "get_open_positions"):
            raw_positions = await client.get_open_positions()
        elif hasattr(client, "futures_account"):
            acc_info = await client.futures_account()
            raw_positions = [
                {
                    "symbol": p.get("symbol"),
                    "side": "LONG" if float(p.get("positionAmt", 0)) > 0 else "SHORT",
                    "position_amt": float(p.get("positionAmt", 0)),
                    "qty": abs(float(p.get("positionAmt", 0))),
                    "entry_price": float(p.get("entryPrice", 0.0)),
                    "unrealized_pnl": float(p.get("unrealizedProfit", 0.0)),
                    "leverage": int(p.get("leverage", bot_config.leverage)),
                    "margin": float(p.get("initialMargin", 0.0)),
                    "mark_price": float(p.get("entryPrice", 0.0)),
                }
                for p in acc_info.get("positions", [])
                if float(p.get("positionAmt", 0)) != 0
            ]

        valid_positions = []
        active_meta = bot_state_ref.setdefault("active_trade_meta", {}) if bot_state_ref is not None else {}

        for p in raw_positions:
            amt = float(p.get("position_amt", p.get("qty", 0.0)))
            if amt == 0:
                continue
            sym = str(p.get("symbol", "")).upper()
            if not sym:
                continue

            side = str(p.get("side", "LONG")).upper()
            entry_p = float(p.get("entry_price", 0.0))
            unreal = float(p.get("unrealized_pnl", 0.0))
            lev = int(p.get("leverage", bot_config.leverage) or bot_config.leverage)
            qty = abs(float(p.get("qty", amt)))
            m_val = float(p.get("margin", 0.0))
            if m_val <= 0 and lev > 0:
                m_val = (qty * entry_p) / lev if entry_p > 0 else 0.0

            valid_positions.append(p)

            # Daftarkan / perbarui active_trade_meta agar bot langsung memantau posisi real yang ada
            if sym not in active_meta or active_meta[sym].get("is_paper"):
                tp_rate = (bot_config.tp_percent / 100) / lev if lev > 0 else 0.02
                sl_rate = (bot_config.sl_percent / 100) / lev if lev > 0 else 0.015
                tp_calc = entry_p * (1 + tp_rate) if side == "LONG" else entry_p * (1 - tp_rate)
                sl_calc = entry_p * (1 - sl_rate) if side == "LONG" else entry_p * (1 + sl_rate)

                ctime_val = p.get("ctime", 0)
                entry_dt = datetime.fromtimestamp(ctime_val / 1000.0) if ctime_val and int(ctime_val) > 0 else datetime.now()

                active_meta[sym] = {
                    "entry_time": entry_dt,
                    "entry_price": entry_p,
                    "side": side,
                    "margin_usdt": m_val,
                    "leverage": lev,
                    "quantity": qty,
                    "tp_price": tp_calc,
                    "sl_price": sl_calc,
                    "exchange": f"{ex_name}_REAL",
                    "is_paper": False,
                    "is_bot_trade": True,
                    "mfe": max(0.0, unreal),
                    "mae": min(0.0, unreal),
                    "pattern_entry_id": None,
                    "alasan": f"Auto-Restored from {ex_name} Real Open Position",
                    "ai_eval_summary": "Pendeteksian posisi exchange saat startup/pindah server",
                }
                logger.info(f"[REAL SYNC] Posisi aktif {sym} ({side}) berhasil dipulihkan ke bot. Margin: ${m_val:.2f}, Entry: {entry_p}")

        result["open_positions"] = valid_positions
        result["open_positions_count"] = len(valid_positions)

    except Exception as e_pos:
        logger.error(f"[REAL SYNC] Gagal fetch real open positions {ex_name}: {e_pos}")

    # 4. Ambil dan Sinkronkan Riwayat Closed Trades ke DB
    if sync_history and hasattr(client, "get_history_positions"):
        try:
            from database.trade_repo import sync_exchange_trades_to_db
            synced = await sync_exchange_trades_to_db(client, limit=history_limit)
            result["synced_trades"] = synced
            result["synced_history_count"] = len(synced)
            if synced:
                logger.info(f"[REAL SYNC] Berhasil sinkron {len(synced)} riwayat trade real dari {ex_name} ke database.")
        except Exception as e_hist:
            logger.error(f"[REAL SYNC] Gagal sinkron history {ex_name}: {e_hist}")

    # 5. Catat Log Scanner & Console
    log_msg = (
        f"🟢 [REAL SYNC] Akun Real {ex_name} Terdeteksi | "
        f"Saldo: ${result['total_wallet_balance']:.2f} USDT (Avail: ${result['available_balance']:.2f}) | "
        f"Posisi Real Terbuka: {result['open_positions_count']} | "
        f"History Baru: {result['synced_history_count']}"
    )
    try:
        from core.scanner_logger import add_scanner_log
        add_scanner_log("INFO", "REAL_ACCOUNT", log_msg)
    except Exception:
        pass
    print(log_msg)

    return result
