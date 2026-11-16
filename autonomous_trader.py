#!/usr/bin/env python3
"""
Autonomous Day Trading Bot for Alpaca Paper Trading
Strategy: Opening Range Breakout (ORB) with strict risk management
- Starting Budget: $1,000
- Risk per trade: $20 (2%)
- Max position: $500 (50%)
- Pre-market screening -> 15-min Opening Range -> 5-min Breakout confirmation -> Alpaca Bracket Order -> 15:55 ET Flatten
"""

import os
import sys
import time
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import deque

# Recent log buffer for web dashboard
recent_logs = deque(maxlen=100)

class LogBufferHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            recent_logs.append(msg)
        except Exception:
            pass

# Logger setup
logger = logging.getLogger("AutonomousTrader")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

buf_handler = LogBufferHandler()
buf_handler.setFormatter(formatter)
logger.addHandler(buf_handler)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")


def load_config():
    """Load risk settings from config.json or environment."""
    cfg = {
        "budget": float(os.environ.get("TRADING_BUDGET", 1000.0)),
        "max_loss_per_trade_usd": float(os.environ.get("MAX_LOSS_PER_TRADE", 20.0)),
        "max_daily_loss_usd": float(os.environ.get("MAX_DAILY_LOSS", 50.0)),
        "max_position_pct": 50.0,
        "max_order_usd": float(os.environ.get("MAX_ORDER_USD", 500.0)),
        "min_reward_risk": 1.5,
        "no_entries_before": os.environ.get("NO_ENTRIES_BEFORE", "09:45"),
        "no_entries_after": os.environ.get("NO_ENTRIES_AFTER", "10:30"),
        "flat_by": os.environ.get("FLAT_BY", "15:55"),
        "paper": os.environ.get("ALPACA_PAPER_TRADE", "true").lower() in ("true", "1", "yes"),
        "tickers": ["NVDA", "AMZN", "INTC", "AAPL", "MSFT", "AMD", "PLTR", "TSLA"]
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                cfg.update(user_cfg)
        except Exception as e:
            logger.warning(f"Could not read config.json: {e}")
    return cfg


def get_alpaca_credentials():
    """Retrieve Alpaca API credentials from environment."""
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")

    if not key or not secret:
        # Fallback to local mcp_config.json if available
        mcp_path = os.path.expanduser("~/.gemini/config/mcp_config.json")
        if os.path.exists(mcp_path):
            try:
                with open(mcp_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    alpaca_env = data.get("mcpServers", {}).get("alpaca", {}).get("env", {})
                    key = key or alpaca_env.get("ALPACA_API_KEY")
                    secret = secret or alpaca_env.get("ALPACA_SECRET_KEY")
            except Exception:
                pass

    if not key or not secret:
        raise ValueError("Missing Alpaca API credentials. Please set ALPACA_API_KEY and ALPACA_SECRET_KEY.")
    return key, secret


def alpaca_request(endpoint, method="GET", body=None):
    """Execute authenticated REST request to Alpaca Paper API."""
    try:
        key, secret = get_alpaca_credentials()
    except Exception as e:
        logger.error(str(e))
        return None

    base_url = "https://paper-api.alpaca.markets/v2"
    url = f"{base_url}/{endpoint.lstrip('/')}"
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Content-Type": "application/json"
    }

    data_bytes = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        logger.error(f"Alpaca API HTTP {e.code} on {endpoint}: {err_msg}")
        return None
    except Exception as e:
        logger.error(f"Alpaca request error on {endpoint}: {e}")
        return None


def get_market_clock():
    """Fetch market clock status from Alpaca."""
    return alpaca_request("/clock")


def get_account_summary():
    """Fetch account summary from Alpaca."""
    return alpaca_request("/account")


def fetch_yahoo_5m_bars(symbol):
    """Fetch recent 5-minute bars from Yahoo Finance chart API."""
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=5m"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data.get("chart", {}).get("result")
            if not result:
                return []
            res0 = result[0]
            timestamps = res0.get("timestamp", [])
            indicators = res0.get("indicators", {}).get("quote", [{}])[0]
            opens = indicators.get("open", [])
            highs = indicators.get("high", [])
            lows = indicators.get("low", [])
            closes = indicators.get("close", [])
            volumes = indicators.get("volume", [])

            bars = []
            for i, ts in enumerate(timestamps):
                if i < len(closes) and closes[i] is not None:
                    bars.append({
                        "time": ts,
                        "open": opens[i],
                        "high": highs[i],
                        "low": lows[i],
                        "close": closes[i],
                        "volume": volumes[i]
                    })
            return bars
    except Exception as e:
        logger.warning(f"Error fetching bars for {symbol}: {e}")
        return []


def send_telegram_alert(message):
    """Send formatted Markdown alert to Telegram."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        cfg = load_config()
        token = token or cfg.get("telegram_bot_token")
        chat_id = chat_id or cfg.get("telegram_chat_id")

    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Telegram notification sent successfully.")
    except Exception as e:
        logger.warning(f"Could not send Telegram message: {e}")


def send_daily_summary(starting_equity, trades_today, symbols_traded):
    """Send daily P&L and paper balance summary to Telegram."""
    acc = get_account_summary() or {}
    current_equity = float(acc.get("portfolio_value", 1000.0))
    cash = float(acc.get("cash", 1000.0))
    bp = float(acc.get("buying_power", 4000.0))
    pnl = current_equity - starting_equity
    pnl_pct = (pnl / starting_equity) * 100 if starting_equity else 0.0
    pnl_sign = "+" if pnl >= 0 else ""
    pnl_emoji = "🟢" if pnl >= 0 else ("🔴" if pnl < 0 else "⚪")

    # Alpaca has $0 commission on US equities. Regulatory fees on sell orders:
    est_fees = round(trades_today * 0.01, 2)
    net_pnl = pnl - est_fees

    ny_date = datetime.now(timezone(timedelta(hours=-5))).strftime('%A, %b %d, %Y')

    msg = f"""📊 *Alpaca Daily Trading Report* 📊
📅 *Date:* {ny_date}

💵 *Paper Funds Available:*
• Portfolio Equity: `${current_equity:,.2f}`
• Tradable Cash: `${cash:,.2f}` (100% Cash)
• Buying Power: `${bp:,.2f}`

📈 *Daily Performance:*
• Daily P&L: {pnl_emoji} *{pnl_sign}${pnl:,.2f} ({pnl_sign}{pnl_pct:.2f}%)*
• Trades Executed: `{trades_today}` ({', '.join(symbols_traded) if symbols_traded else 'None'})
• Overnight Risk: `$0.00` (All Flattened)

🏷️ *Brokerage & Fees:*
• Alpaca Commission: `$0.00` (Zero Commission)
• Est. Regulatory Fees: `${est_fees:.2f}`
• Net P&L: *{pnl_sign}${net_pnl:,.2f}*

🛡️ *Account Status:* ACTIVE (Paper Trading)"""

    send_telegram_alert(msg)


def find_stop_order(open_orders, symbol):
    """Find the open stop-loss order for a given symbol, searching top-level orders and nested legs."""
    if not open_orders:
        return None
    for o in open_orders:
        if o.get("symbol") == symbol and o.get("side") == "sell":
            if o.get("type") == "stop" or o.get("order_type") == "stop" or o.get("stop_price") is not None:
                return o
        for leg in o.get("legs") or []:
            if leg.get("symbol") == symbol and leg.get("side") == "sell":
                if leg.get("type") == "stop" or leg.get("order_type") == "stop" or leg.get("stop_price") is not None:
                    return leg
    return None


def check_and_update_breakeven(active_trades):
    """
    Check if any active position has gained +1R.
    If so, automatically update the open stop-loss order on Alpaca to entry price + $0.02 (Break-Even).
    """
    if active_trades is None:
        return

    positions = alpaca_request("/positions") or []
    pos_dict = {p.get("symbol"): p for p in positions}
    open_orders = alpaca_request("/orders?status=open") or []

    # Clean up closed positions from active_trades
    for sym in list(active_trades.keys()):
        if sym not in pos_dict:
            logger.info(f"Position for {sym} is no longer open (closed/stopped/target hit). Removing from active monitoring.")
            del active_trades[sym]

    # Auto-adopt active positions from Alpaca if not tracked yet (e.g. after restart or failover)
    for sym, pos in pos_dict.items():
        if sym not in active_trades:
            stop_order = find_stop_order(open_orders, sym)
            if stop_order:
                stop_px = float(stop_order.get("stop_price") or 0.0)
                entry_px = float(pos.get("avg_entry_price") or 0.0)
                if stop_px > 0 and entry_px > 0:
                    be_already_set = stop_px >= round(entry_px + 0.02, 2)
                    active_trades[sym] = {
                        "entry": entry_px,
                        "initial_stop": stop_px,
                        "target": 0.0,
                        "be_moved": be_already_set
                    }
                    status_note = "with BE already active" if be_already_set else f"SL: ${stop_px:.2f}"
                    logger.info(f"Adopted active trade for {sym} (Entry: ${entry_px:.2f}, {status_note})")

    for symbol, trade in list(active_trades.items()):
        if trade.get("be_moved"):
            continue

        pos = pos_dict.get(symbol)
        if not pos:
            continue

        entry_px = float(pos.get("avg_entry_price", trade.get("entry", 0.0)))
        initial_stop = float(trade.get("initial_stop", 0.0))
        risk = entry_px - initial_stop

        if risk <= 0:
            continue

        current_px = float(pos.get("current_price", 0.0))
        target_1r = entry_px + risk

        # Has the stock gained +1R?
        if current_px >= target_1r:
            new_stop = round(entry_px + 0.02, 2)  # Entry + 2 cents to cover regulatory fees
            stop_order = find_stop_order(open_orders, symbol)

            if stop_order and "id" in stop_order:
                current_stop = float(stop_order.get("stop_price") or 0.0)
                if current_stop >= new_stop:
                    trade["be_moved"] = True
                    logger.info(f"Stop-Loss for {symbol} is already at/above break-even (${current_stop:.2f} >= ${new_stop:.2f}). Marked active.")
                    continue

                stop_order_id = stop_order["id"]
                logger.info(f"*** +1R REACHED FOR {symbol} (${current_px:.2f} >= ${target_1r:.2f}) ***")
                logger.info(f"Modifying Stop-Loss order {stop_order_id[:8]} from ${initial_stop:.2f} to Break-Even (${new_stop:.2f})...")

                patch_payload = {"stop_price": f"{new_stop:.2f}"}
                resp = alpaca_request(f"/orders/{stop_order_id}", method="PATCH", body=patch_payload)

                if resp and "id" in resp:
                    trade["be_moved"] = True
                    logger.info(f"[SUCCESS] Break-Even Stop confirmed on Alpaca! Updated Order ID: {resp['id']}")
                    send_telegram_alert(
                        f"🛡️ *Break-Even Protection Activated!*\n"
                        f"• Stock: *{symbol}*\n"
                        f"• Current Price: *${current_px:.2f}* (+1R Milestone reached)\n"
                        f"• Stop Moved To: *${new_stop:.2f}* (Entry + $0.02)\n"
                        f"• Remaining Risk: *$0.00 (Risk-Free Trade!)*"
                    )
                else:
                    logger.error(f"[ERROR] Failed to update Stop-Loss order {stop_order_id} for {symbol} to ${new_stop:.2f}")


def submit_bracket_order(symbol, qty, entry_price, stop_price, target_price):
    """Submit a bracket order to Alpaca Paper Trading API with idempotent client_order_id."""
    today_str = datetime.now(timezone(timedelta(hours=-5))).strftime("%Y%m%d")
    client_id = f"ORB_{symbol}_{today_str}_{int(entry_price * 100)}"

    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "order_class": "bracket",
        "client_order_id": client_id,
        "take_profit": {
            "limit_price": f"{target_price:.2f}"
        },
        "stop_loss": {
            "stop_price": f"{stop_price:.2f}"
        }
    }
    logger.info(f"Submitting Bracket Order: BUY {qty} {symbol} (Market Entry ~${entry_price:.2f}, SL=${stop_price:.2f}, TP=${target_price:.2f})")
    resp = alpaca_request("/orders", method="POST", body=payload)
    if resp and "id" in resp:
        logger.info(f"[SUCCESS] Order Placed! Broker Order ID: {resp['id']}, Status: {resp.get('status')}")
        send_telegram_alert(
            f"🎯 *Trade Executed (Alpaca Paper)*\n"
            f"• Action: *BUY {qty} {symbol}*\n"
            f"• Est. Entry: `${entry_price:.2f}`\n"
            f"• Stop-Loss: `${stop_price:.2f}`\n"
            f"• Take-Profit: `${target_price:.2f}`\n"
            f"• Risk at Stop: `${qty * (entry_price - stop_price):.2f}`"
        )
        return resp
    else:
        logger.error(f"[FAILED] Order placement failed for {symbol}")
        return None


def flatten_all_positions(max_wait_seconds=60):
    """Liquidate all open positions and cancel open orders at end of day.
    Uses ?cancel_orders=true and polls until all positions are confirmed 0.
    Does NOT call DELETE /orders immediately or on timeout, preventing race condition cancellation of liquidation market orders.
    """
    logger.info("Auto-flatten triggered: Closing all open positions (cancel_orders=true)...")
    resp = alpaca_request("/positions?cancel_orders=true", method="DELETE")
    logger.info(f"Liquidation response: {resp}")

    start_time = time.time()
    confirmed_zero = False
    re_attempted = False

    while time.time() - start_time < max_wait_seconds:
        positions = alpaca_request("/positions")
        if positions is None:
            time.sleep(2)
            continue
        if len(positions) == 0:
            confirmed_zero = True
            logger.info(f"Auto-flatten confirmed: 0 open positions remaining after {int(time.time() - start_time)}s.")
            break

        # If still open after 20s, re-send liquidation request in case first call timed out/dropped
        if not re_attempted and (time.time() - start_time) > 20:
            logger.warning(f"{len(positions)} positions still liquidating after 20s. Re-issuing DELETE /positions?cancel_orders=true...")
            alpaca_request("/positions?cancel_orders=true", method="DELETE")
            re_attempted = True

        logger.info(f"Auto-flatten in progress: {len(positions)} positions still liquidating... waiting 2s")
        time.sleep(2)

    if confirmed_zero:
        # Verify and clear any remaining open orders strictly after positions are confirmed 0
        open_orders = alpaca_request("/orders?status=open") or []
        if open_orders:
            logger.info(f"Canceling {len(open_orders)} remaining open orders after positions confirmed closed...")
            alpaca_request("/orders", method="DELETE")
        else:
            logger.info("All open orders confirmed cleared.")
    else:
        logger.error(
            f"Auto-flatten verification timed out after {max_wait_seconds}s! "
            f"Skipping DELETE /orders to prevent canceling active liquidation orders."
        )


def run_session(config):
    """Run one continuous trading day session."""
    logger.info("=" * 60)
    logger.info("Autonomous Trading Session Started")
    logger.info(f"Budget: ${config['budget']} | Max Loss/Trade: ${config['max_loss_per_trade_usd']} | Max Position: ${config['max_order_usd']}")
    logger.info("=" * 60)

    symbols = config.get("tickers", ["NVDA", "AMZN", "INTC", "AAPL", "MSFT", "AMD", "PLTR", "TSLA"])
    traded_symbols = set()
    active_trades = {}
    trades_today = 0
    max_trades = config.get("max_trades_per_day", 3)
    acc0 = get_account_summary() or {}
    starting_equity = float(acc0.get("portfolio_value", config['budget']))

    while True:
        clock = get_market_clock()
        if not clock:
            logger.warning("Could not reach Alpaca Clock API. Retrying in 30s...")
            time.sleep(30)
            continue

        is_open = clock.get("is_open", False)
        now_str = clock.get("timestamp", "")
        next_open_str = clock.get("next_open", "")

        try:
            now_dt = datetime.fromisoformat(now_str)
            time_et = now_dt.strftime("%H:%M")
        except Exception:
            time_et = datetime.now(timezone(timedelta(hours=-5))).strftime("%H:%M")

        # Case 1: Market is closed
        if not is_open:
            logger.info(f"Market is CLOSED (Current NY Time: {time_et}). Next open: {next_open_str}")
            try:
                next_open_dt = datetime.fromisoformat(next_open_str)
                sleep_seconds = (next_open_dt - datetime.now(timezone.utc)).total_seconds() - 300
                if sleep_seconds > 60:
                    logger.info(f"Sleeping for {int(sleep_seconds // 60)} minutes until market open...")
                    time.sleep(min(sleep_seconds, 1800))
                    continue
            except Exception as e:
                logger.warning(f"Error parsing next open: {e}")
            time.sleep(60)
            continue

        # Case 2: Market open, establishing opening range (09:30 - 09:45)
        if time_et < config.get("no_entries_before", "09:45"):
            logger.info(f"Market OPEN (NY {time_et}). Establishing 15-minute Opening Range (09:30 - 09:45)...")
            time.sleep(60)
            continue

        # Case 3: Flat-by time reached (>= 15:55)
        flat_by_time = config.get("flat_by", "15:55")
        if time_et >= flat_by_time:
            logger.info(f"Cutoff time reached (NY {time_et} >= {flat_by_time}). Flattening portfolio...")
            flatten_all_positions()
            time.sleep(5)
            send_daily_summary(starting_equity, trades_today, list(traded_symbols))
            logger.info("Session complete for today. Waiting until market close...")
            while True:
                clk = get_market_clock()
                if not clk or not clk.get("is_open", False):
                    break
                time.sleep(30)
            break

        # Check and manage open trades (Move stop to Break-Even at +1R)
        check_and_update_breakeven(active_trades)

        # Case 4: Past entry cutoff (>= 10:30)
        cutoff_time = config.get("no_entries_after", "10:30")
        if time_et >= cutoff_time:
            logger.info(f"Time {time_et} past entry cutoff ({cutoff_time}). Monitoring open positions until {config.get('flat_by', '15:55')}...")
            time.sleep(60)
            continue

        # Case 5: Daily trade limit reached
        if trades_today >= max_trades:
            logger.info(f"Daily trade limit reached ({trades_today}/{max_trades}). Monitoring...")
            time.sleep(120)
            continue

        # Failover Check: If running on Cloud and Laptop is active, stay in Standby
        is_laptop_fn = getattr(sys.modules.get("autonomous_trader"), "is_laptop_active", None)
        if is_laptop_fn and is_laptop_fn():
            logger.info(f"[STANDBY] Laptop heartbeat is active. Cloud holding back in standby (NY {time_et})...")
            time.sleep(30)
            continue

        logger.info(f"[Scanning] Active session NY {time_et}. Evaluating {len(symbols)} tickers for ORB breakout...")

        for sym in symbols:
            if sym in traded_symbols:
                continue

            bars = fetch_yahoo_5m_bars(sym)
            if len(bars) < 4:
                continue

            or_bars = bars[:3]
            or_high = max(b["high"] for b in or_bars)
            or_low = min(b["low"] for b in or_bars)
            or_mid = (or_high + or_low) / 2.0

            latest_bar = bars[-1]
            latest_close = latest_bar["close"]

            if latest_close > or_high:
                entry_price = latest_close

                # Compute Average True Range (ATR) across the 15-minute bars
                trs = []
                for i in range(len(or_bars)):
                    tr = or_bars[i]["high"] - or_bars[i]["low"]
                    if i > 0:
                        prev_c = or_bars[i - 1]["close"]
                        tr = max(tr, abs(or_bars[i]["high"] - prev_c), abs(or_bars[i]["low"] - prev_c))
                    trs.append(tr)
                atr = (sum(trs) / len(trs)) if trs else (or_high - or_low) / 2.0

                max_risk_usd = float(config.get("max_loss_per_trade_usd", 20.0))
                max_pos_usd = float(config.get("max_order_usd", 500.0))

                # Structural Volatility-Buffered Stop:
                # Use Range Low if reasonable risk, else entry - 1.5 * ATR
                range_low_risk = entry_price - or_low
                if range_low_risk > 0.05 and (max_risk_usd / range_low_risk) >= 1:
                    stop_price = round(or_low - 0.02, 2)
                else:
                    stop_price = round(entry_price - max((or_high - or_mid), 1.5 * atr), 2)

                risk_per_share = entry_price - stop_price
                if risk_per_share <= 0.05:
                    continue

                shares_by_risk = int(max_risk_usd / risk_per_share)
                shares_by_cap = int(max_pos_usd / entry_price)
                shares = min(shares_by_risk, shares_by_cap)

                if shares < 1:
                    logger.info(f"{sym} breakout at ${entry_price:.2f}, but share size < 1 under $20 risk limit.")
                    continue

                target_price = round(entry_price + (risk_per_share * float(config.get("min_reward_risk", 1.5))), 2)

                logger.info(f"*** BREAKOUT DETECTED: {sym} ***")
                logger.info(f"Range: [${or_low:.2f} - ${or_high:.2f}] | ATR: ${atr:.2f} | Breakout Close: ${entry_price:.2f}")
                logger.info(f"Size: {shares} shares | Risk: ${shares * risk_per_share:.2f} | SL: ${stop_price:.2f} | TP: ${target_price:.2f}")

                order = submit_bracket_order(sym, shares, entry_price, stop_price, target_price)
                if order:
                    traded_symbols.add(sym)
                    active_trades[sym] = {
                        "entry": entry_price,
                        "initial_stop": stop_price,
                        "target": target_price,
                        "be_moved": False
                    }
                    trades_today += 1

                if trades_today >= max_trades:
                    break

        time.sleep(60)


def start_trading_loop():
    """Continuous worker entry point."""
    config = load_config()
    logger.info("Initializing Autonomous Trading Engine...")
    while True:
        try:
            run_session(config)
            time.sleep(60)
        except Exception as e:
            logger.exception(f"Unexpected error in trading loop: {e}")
            time.sleep(60)


if __name__ == "__main__":
    start_trading_loop()
