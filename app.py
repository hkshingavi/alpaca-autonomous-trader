#!/usr/bin/env python3
"""
Web Server & Health Dashboard for Alpaca Autonomous Day Trader
Compatible with Koyeb, Render, Railway, Docker, and local execution.
Includes Heartbeat Failover System (Laptop Primary / Cloud Standby).
"""

import os
import sys
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import autonomous_trader

PORT = int(os.environ.get("PORT", 8000))
last_laptop_heartbeat = 0.0

def is_laptop_active():
    """Returns True if laptop sent a heartbeat within last 90 seconds."""
    global last_laptop_heartbeat
    return (time.time() - last_laptop_heartbeat) < 90

# Expose to autonomous_trader module
autonomous_trader.is_laptop_active = is_laptop_active


class DashboardHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        global last_laptop_heartbeat
        if self.path == "/heartbeat":
            last_laptop_heartbeat = time.time()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            status = {
                "status": "ok",
                "role": "cloud_standby",
                "laptop_status": "active_primary",
                "timestamp": autonomous_trader.datetime.now(autonomous_trader.timezone.utc).isoformat()
            }
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            status = {
                "status": "healthy",
                "service": "alpaca-autonomous-trader",
                "laptop_active": is_laptop_active(),
                "cloud_role": "standby" if is_laptop_active() else "active_primary",
                "timestamp": autonomous_trader.datetime.now(autonomous_trader.timezone.utc).isoformat()
            }
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        # Main Dashboard Page
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        clock = autonomous_trader.get_market_clock() or {}
        is_open = clock.get("is_open", False)
        next_open = clock.get("next_open", "Unknown")
        now_str = clock.get("timestamp", "Unknown")

        account = autonomous_trader.get_account_summary() or {}
        cash = account.get("cash", "1000.00")
        portfolio_val = account.get("portfolio_value", "1000.00")
        buying_power = account.get("buying_power", "4000.00")
        acc_status = account.get("status", "ACTIVE")

        cfg = autonomous_trader.load_config()
        tickers = cfg.get("tickers", [])

        logs_html = "".join(f"<div class='log-line'>{line}</div>" for line in reversed(autonomous_trader.recent_logs))
        if not logs_html:
            logs_html = "<div class='log-line text-muted'>Initializing logs...</div>"

        status_badge = "<span class='badge bg-success'>MARKET OPEN</span>" if is_open else "<span class='badge bg-secondary'>MARKET CLOSED</span>"

        if is_laptop_active():
            sec_ago = int(time.time() - last_laptop_heartbeat)
            failover_badge = f"<span class='badge bg-warning text-dark'>💻 PRIMARY: LAPTOP (Ping {sec_ago}s ago) | CLOUD: STANDBY</span>"
        else:
            failover_badge = "<span class='badge bg-primary'>☁️ PRIMARY: CLOUD ACTIVE (Laptop Offline / Asleep)</span>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Alpaca Autonomous Day Trader</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <meta http-equiv="refresh" content="15">
    <style>
        body {{ background-color: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        .card {{ background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; }}
        .card-header {{ background-color: #21262d; border-bottom: 1px solid #30363d; color: #f0f6fc; font-weight: 600; }}
        .log-box {{ background-color: #090d13; border: 1px solid #30363d; border-radius: 6px; padding: 12px; height: 350px; overflow-y: auto; font-family: monospace; font-size: 0.85rem; }}
        .log-line {{ color: #7ee787; border-bottom: 1px solid #21262d; padding: 2px 0; }}
        .stat-value {{ font-size: 1.5rem; font-weight: 700; color: #58a6ff; }}
        .ticker-pill {{ display: inline-block; background: #238636; color: #fff; padding: 3px 8px; border-radius: 12px; font-size: 0.8rem; margin: 2px; }}
    </style>
</head>
<body class="py-4">
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4 pb-2 border-bottom border-secondary">
            <div>
                <h3 class="mb-0 text-white">⚡ Alpaca Autonomous Day Trader</h3>
                <small class="text-secondary">Hybrid Failover: Laptop & Render Cloud | Opening Range Breakout (ORB)</small>
            </div>
            <div>
                {failover_badge}
                {status_badge}
            </div>
        </div>

        <!-- Metric Cards -->
        <div class="row g-3 mb-4">
            <div class="col-md-3">
                <div class="card p-3">
                    <small class="text-secondary">Paper Portfolio Value</small>
                    <div class="stat-value">${float(portfolio_val):,.2f}</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3">
                    <small class="text-secondary">Tradable Cash</small>
                    <div class="stat-value">${float(cash):,.2f}</div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3">
                    <small class="text-secondary">Risk Per Trade</small>
                    <div class="stat-value">${cfg['max_loss_per_trade_usd']} <span style="font-size:0.9rem; color:#8b949e">(2%)</span></div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3">
                    <small class="text-secondary">Max Position Size</small>
                    <div class="stat-value">${cfg['max_order_usd']} <span style="font-size:0.9rem; color:#8b949e">(50%)</span></div>
                </div>
            </div>
        </div>

        <div class="row g-3 mb-4">
            <div class="col-md-8">
                <div class="card">
                    <div class="card-header d-flex justify-content-between align-items-center">
                        <span>Live Trading Activity & Logs</span>
                        <span class="badge bg-dark border border-secondary text-secondary">Auto-refreshes every 15s</span>
                    </div>
                    <div class="card-body">
                        <div class="log-box">
                            {logs_html}
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card mb-3">
                    <div class="card-header">Market Session & Clock</div>
                    <div class="card-body">
                        <p class="mb-1"><strong>Status:</strong> {acc_status}</p>
                        <p class="mb-1"><strong>Clock:</strong> {now_str[:19] if len(now_str) >= 19 else now_str}</p>
                        <p class="mb-0"><strong>Next Open:</strong> {next_open[:19] if len(next_open) >= 19 else next_open}</p>
                    </div>
                </div>
                <div class="card">
                    <div class="card-header">Active Candidate Basket</div>
                    <div class="card-body">
                        <p class="small text-secondary mb-2">Monitored for 15-minute Opening Range Breakouts:</p>
                        <div>
                            {"".join(f"<span class='ticker-pill'>{t}</span>" for t in tickers)}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        return


def start_server():
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    autonomous_trader.logger.info(f"Dashboard server listening on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    t = threading.Thread(target=autonomous_trader.start_trading_loop, daemon=True)
    t.start()
    start_server()
