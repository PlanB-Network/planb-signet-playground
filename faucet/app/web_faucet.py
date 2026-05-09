#!/usr/bin/env python3
"""
Web Bitcoin Signet Faucet
A simple web interface for the Signet faucet to onboard newbies.
"""

import os
import random
import subprocess
import json
import sqlite3
import time
import threading
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from flask import Flask, request, render_template_string, session, redirect, url_for, jsonify
from bitcoinrpc.authproxy import AuthServiceProxy, JSONRPCException
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {'1', 'true', 'yes', 'on'}

def env_int(name, default):
    return int(os.getenv(name, str(default)))

def env_decimal(name, default):
    return Decimal(os.getenv(name, str(default)))

def required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f'{name} must be set in the environment')
    return value

if env_bool('ALLOW_INSECURE_DEV_SECRET'):
    app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-only-signet-faucet-secret')
else:
    app.secret_key = required_env('FLASK_SECRET_KEY')

DB_PATH = os.getenv('DB_PATH', './faucet.db')
LOG_DIR = os.getenv('LOG_DIR', './logs')
RATE_LIMIT_HOURS = env_int('RATE_LIMIT_HOURS', 24)
BTC_DAILY_LIMIT_SATS = env_int('BTC_DAILY_LIMIT_SATS', 1_000_000)
LN_DAILY_LIMIT_SATS = env_int('LN_DAILY_LIMIT_SATS', 100_000)
EXPLORER_URL = os.getenv('EXPLORER_URL', 'https://mempool-signet.planb.academy')

# RPC connection
RPC_USER = required_env('RPC_USER')
RPC_PASSWORD = required_env('RPC_PASSWORD')
RPC_HOST = os.getenv('RPC_HOST', '127.0.0.1')
RPC_PORT = env_int('RPC_PORT', 38332)
RPC_WALLET = os.getenv('RPC_WALLET', 'test-wallet')

# Faucet settings
AMOUNT = env_decimal('BTC_AMOUNT', '0.001')
LN_MAX_SATS = env_int('LN_MAX_SATS', 10000)
LNCLI_BIN = os.getenv('LNCLI_BIN', 'lncli')
LND_DIR = os.getenv('LND_DIR', str(Path.home() / '.lnd-signet'))
LND_RPCSERVER = os.getenv('LND_RPCSERVER', '127.0.0.1:10010')
LNCLI_BASE = [LNCLI_BIN, f'--lnddir={LND_DIR}', f'--rpcserver={LND_RPCSERVER}']

DB_LOCK = threading.Lock()
SEND_LOCK = threading.Lock()
SATOSHIS_PER_BTC = Decimal('100000000')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    with closing(get_db()) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS btc_requests (address TEXT, ip TEXT, amount_sats INTEGER DEFAULT 0, txid TEXT, ts INTEGER)')
        conn.execute('CREATE TABLE IF NOT EXISTS ln_requests (ip TEXT, amount_sats INTEGER DEFAULT 0, payment_hash TEXT, ts INTEGER)')
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(btc_requests)')}
        if 'ip' not in columns:
            conn.execute('ALTER TABLE btc_requests ADD COLUMN ip TEXT')
        if 'amount_sats' not in columns:
            conn.execute('ALTER TABLE btc_requests ADD COLUMN amount_sats INTEGER DEFAULT 0')
        if 'txid' not in columns:
            conn.execute('ALTER TABLE btc_requests ADD COLUMN txid TEXT')
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(ln_requests)')}
        if 'amount_sats' not in columns:
            conn.execute('ALTER TABLE ln_requests ADD COLUMN amount_sats INTEGER DEFAULT 0')
        if 'payment_hash' not in columns:
            conn.execute('ALTER TABLE ln_requests ADD COLUMN payment_hash TEXT')
        conn.commit()

def is_btc_rate_limited(address, ip):
    if RATE_LIMIT_HOURS <= 0:
        return False
    cutoff = int(time.time()) - RATE_LIMIT_HOURS * 3600
    with closing(get_db()) as conn:
        row = conn.execute(
            'SELECT ts FROM btc_requests WHERE (address=? OR ip=?) AND ts>? LIMIT 1',
            (address, ip, cutoff),
        ).fetchone()
    return row is not None

def record_btc_request(address, ip, amount_sats, txid):
    with DB_LOCK, closing(get_db()) as conn:
        conn.execute(
            'INSERT INTO btc_requests (address, ip, amount_sats, txid, ts) VALUES (?,?,?,?,?)',
            (address, ip, amount_sats, txid, int(time.time())),
        )
        conn.commit()

def is_ln_rate_limited(ip):
    if RATE_LIMIT_HOURS <= 0:
        return False
    cutoff = int(time.time()) - RATE_LIMIT_HOURS * 3600
    with closing(get_db()) as conn:
        row = conn.execute('SELECT ts FROM ln_requests WHERE ip=? AND ts>?', (ip, cutoff)).fetchone()
    return row is not None

def record_ln_request(ip, amount_sats, payment_hash=None):
    with DB_LOCK, closing(get_db()) as conn:
        conn.execute(
            'INSERT INTO ln_requests (ip, amount_sats, payment_hash, ts) VALUES (?,?,?,?)',
            (ip, amount_sats, payment_hash, int(time.time())),
        )
        conn.commit()

def distributed_today(table):
    today_start = int(time.time()) - (int(time.time()) % 86400)
    with closing(get_db()) as conn:
        row = conn.execute(f'SELECT COALESCE(SUM(amount_sats), 0) AS total FROM {table} WHERE ts>=?', (today_start,)).fetchone()
    return int(row['total'])

def last_drips(limit=10):
    with closing(get_db()) as conn:
        btc = [dict(row) for row in conn.execute('SELECT "btc" AS type, address, ip, amount_sats, txid, ts FROM btc_requests ORDER BY ts DESC LIMIT ?', (limit,))]
        ln = [dict(row) for row in conn.execute('SELECT "ln" AS type, NULL AS address, ip, amount_sats, payment_hash AS txid, ts FROM ln_requests ORDER BY ts DESC LIMIT ?', (limit,))]
    return sorted(btc + ln, key=lambda row: row['ts'], reverse=True)[:limit]

init_db()

def get_rpc_connection():
    wallet_part = f'/wallet/{RPC_WALLET}' if RPC_WALLET else ''
    host = f'[{RPC_HOST}]' if ':' in RPC_HOST and not RPC_HOST.startswith('[') else RPC_HOST
    return AuthServiceProxy(f"http://{RPC_USER}:{RPC_PASSWORD}@{host}:{RPC_PORT}{wallet_part}")

def validate_address(rpc, address):
    try:
        info = rpc.validateaddress(address)
        return info['isvalid'] and not info.get('isscript', False)
    except:
        return False

def generate_captcha():
    code = str(random.randint(1000, 9999))
    session['captcha'] = code
    return code

def verify_captcha(user_input):
    return user_input.strip() == session.get('captcha', '')

def generate_ln_captcha():
    code = str(random.randint(1000, 9999))
    session['ln_captcha'] = code
    return code

def verify_ln_captcha(user_input):
    return user_input.strip() == session.get('ln_captcha', '')

def btc_amount_sats():
    return int(AMOUNT * SATOSHIS_PER_BTC)

def client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()

def send_coins(rpc, address):
    try:
        with SEND_LOCK:
            balance = Decimal(str(rpc.getbalance()))
            if balance < AMOUNT:
                return None, f"Insufficient funds. Balance: {balance} signet BTC"
            txid = rpc.sendtoaddress(address, float(AMOUNT))
        return txid, None
    except JSONRPCException as e:
        return None, str(e)

@app.after_request
def set_security_headers(response):
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'")
    return response

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Bitcoin Signet Faucet</title>
    <style>
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            max-width: 800px; 
            margin: 0 auto; 
            padding: 20px; 
            background: linear-gradient(135deg, #fff8e1 0%, #ffecb3 25%, #ffe082 50%, #ffca28 75%, #ffb300 100%);
            background-attachment: fixed;
            background-size: cover;
            color: #333;
            min-height: 100vh;
            position: relative;
        }
        .container { 
            background: rgba(255, 255, 255, 0.95);
            padding: 30px; 
            border-radius: 15px; 
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            border: 2px solid #f7931a;
            margin: 20px auto;
        }
        h1 { 
            color: #f7931a; 
            text-align: center; 
            margin-bottom: 20px;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.1);
        }
        p { 
            line-height: 1.6; 
            color: #555;
        }
        ul { 
            background: #fff3cd; 
            padding: 15px; 
            border-radius: 8px; 
            border-left: 5px solid #f7931a;
        }
        input[type="text"] { 
            width: 100%; 
            padding: 12px; 
            margin: 15px 0; 
            border: 2px solid #ddd; 
            border-radius: 8px; 
            font-size: 16px;
            transition: border-color 0.3s;
        }
        input[type="text"]:focus { 
            border-color: #f7931a; 
            outline: none;
        }
        button { 
            background: linear-gradient(135deg, #f7931a 0%, #ff9800 100%);
            color: white; 
            padding: 12px 25px; 
            border: none; 
            border-radius: 8px; 
            cursor: pointer; 
            font-size: 16px;
            font-weight: bold;
            transition: transform 0.2s, box-shadow 0.2s;
            box-shadow: 0 4px 15px rgba(247, 147, 26, 0.3);
        }
        button:hover { 
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(247, 147, 26, 0.4);
        }
        .message { 
            margin: 15px 0; 
            padding: 15px; 
            border-radius: 8px; 
            font-weight: bold;
        }
        .success { 
            background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%);
            color: #155724; 
            border: 1px solid #c3e6cb;
        }
        .error { 
            background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%);
            color: #721c24; 
            border: 1px solid #f5c6cb;
        }
        a { 
            color: #f7931a; 
            text-decoration: none;
        }
        a:hover { 
            text-decoration: underline;
        }
        .main-menu {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 20px;
            margin: 25px 0;
        }
        .menu-card {
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-height: 180px;
            padding: 28px;
            border-radius: 22px;
            border: 2px solid rgba(247, 147, 26, 0.23);
            background: rgba(255, 255, 255, 0.95);
            box-shadow: 0 18px 35px rgba(0, 0, 0, 0.08);
            cursor: pointer;
            transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
            text-align: left;
        }
        .menu-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 24px 45px rgba(0, 0, 0, 0.12);
            border-color: rgba(247, 147, 26, 0.45);
        }
        .menu-card h2 {
            margin: 0 0 12px;
            font-size: 1.6rem;
            color: #d35400;
        }
        .menu-icon {
            font-size: 4.5rem;
            line-height: 1;
            margin: 18px 0;
            color: #f7931a;
            text-align: center;
        }
        .menu-card p {
            margin: 0;
            color: #555;
            line-height: 1.7;
        }
        .menu-card .subtext {
            margin-top: 12px;
            font-size: 0.98rem;
            color: #777;
        }
        .hidden {
            display: none;
        }
        .section-wrapper {
            background: rgba(255, 255, 255, 0.98);
            padding: 25px;
            border-radius: 20px;
            border: 1px solid rgba(247, 147, 26, 0.18);
            margin-top: 20px;
        }
        .back-button {
            background: transparent;
            color: #f7931a;
            border: 2px solid #f7931a;
            border-radius: 12px;
            padding: 10px 18px;
            font-size: 0.95rem;
            cursor: pointer;
            margin-bottom: 20px;
        }
        .back-button:hover {
            background: #f7931a;
            color: white;
        }
        .hint-btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 22px;
            height: 22px;
            border-radius: 50%;
            border: 2px solid currentColor;
            background: transparent;
            font-size: 13px;
            font-weight: bold;
            cursor: pointer;
            margin-left: 10px;
            padding: 0;
            vertical-align: middle;
            line-height: 1;
            box-shadow: none;
            transform: none;
        }
        .hint-btn:hover {
            transform: none;
            box-shadow: none;
            opacity: 0.75;
        }
        .hint-box {
            display: none;
            margin-top: 10px;
            padding: 12px 15px;
            background: #fff9f0;
            border-left: 4px solid #f7931a;
            border-radius: 6px;
            font-size: 0.92rem;
            font-weight: normal;
            color: #555;
            line-height: 1.6;
        }
        .captcha-box {
            display: inline-block;
            background: #f0f0f0;
            border: 2px dashed #f7931a;
            border-radius: 8px;
            padding: 12px 30px;
            font-size: 2.6rem;
            font-weight: bold;
            letter-spacing: 16px;
            color: #222;
            margin: 10px 0 6px;
            user-select: none;
            font-family: 'Courier New', monospace;
        }
        .captcha-label {
            font-size: 0.9rem;
            color: #777;
            margin-bottom: 4px;
        }
        .ui-guide {
            margin: 16px 0 8px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        .ui-step {
            display: flex;
            gap: 16px;
            align-items: flex-start;
        }
        .ui-step-label {
            background: #f7931a;
            color: white;
            font-weight: bold;
            font-size: 0.8rem;
            border-radius: 50%;
            min-width: 28px;
            height: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-top: 4px;
        }
        .ui-step-content {
            flex: 1;
        }
        .ui-step-content p {
            margin: 0 0 8px;
            font-size: 0.93rem;
        }
        .ui-mockup {
            background: #f0f0f0;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 18px rgba(0,0,0,0.2);
            font-family: 'Segoe UI', sans-serif;
            font-size: 0.85rem;
            color: #222;
            max-width: 540px;
            border: 1px solid #bbb;
        }
        .ui-titlebar {
            background: #e0e0e0;
            padding: 6px 12px;
            display: flex;
            align-items: center;
            gap: 8px;
            border-bottom: 1px solid #bbb;
        }
        .ui-titlebar-text {
            flex: 1;
            font-size: 0.78rem;
            color: #555;
        }
        .ui-win-btn {
            width: 16px; height: 16px;
            border-radius: 3px;
            background: #c0c0c0;
            border: 1px solid #999;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 0.65rem;
            color: #555;
            margin-left: 3px;
        }
        .mock-titlebar-win {
            background: #0078D4; color: white;
            padding: 5px 10px; font-size: 0.8rem;
            display: flex; align-items: center; justify-content: space-between;
        }
        .mock-titlebar-mac {
            background: #e0e0e0; border-bottom: 1px solid #bbb;
            padding: 7px 12px; font-size: 0.8rem; color: #555;
            display: flex; align-items: center; gap: 6px;
        }
        .mock-titlebar-linux {
            background: #2d2d2d; color: #ccc;
            padding: 5px 12px; font-size: 0.78rem;
            display: flex; align-items: center; gap: 6px;
        }
        .mock-body { background: white; padding: 16px 18px; font-size: 0.85rem; }
        .mock-terminal { background: #1e1e1e; color: #d4d4d4; padding: 12px 14px; font-family: monospace; font-size: 0.82rem; line-height: 1.7; }
        .mock-dot { width: 12px; height: 12px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
        .mock-btn { padding: 4px 14px; border-radius: 3px; font-size: 0.82rem; cursor: default; font-family: inherit; border: none; }
        .mock-btn-blue { background: #0078D4; color: white; }
        .mock-btn-grey { background: #e0e0e0; color: #333; border: 1px solid #bbb !important; }
        .mock-btn-mac { background: #007AFF; color: white; border-radius: 6px; }
        .ui-menubar {
            background: #f5f5f5;
            padding: 3px 10px;
            display: flex;
            gap: 2px;
            border-bottom: 1px solid #ccc;
        }
        .ui-menu-item {
            padding: 3px 10px;
            border-radius: 3px;
            cursor: default;
            color: #333;
            font-size: 0.82rem;
        }
        .ui-menu-item.active {
            background: #d0d8e8;
            color: #000;
        }
        .ui-sparrow-layout {
            display: flex;
            min-height: 200px;
        }
        .ui-sidebar {
            background: #2196c4;
            width: 80px;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding-top: 10px;
            gap: 0;
        }
        .ui-sidebar-item {
            width: 100%;
            padding: 12px 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
            color: rgba(255,255,255,0.7);
            font-size: 0.72rem;
            cursor: default;
        }
        .ui-sidebar-item.active {
            background: #1a6f9a;
            color: white;
        }
        .ui-sidebar-icon { font-size: 1.2rem; }
        .ui-content {
            flex: 1;
            background: white;
            padding: 16px 18px;
        }
        .ui-content h4 {
            margin: 0 0 10px;
            font-size: 0.95rem;
            color: #222;
        }
        .ui-type-row {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 14px;
        }
        .ui-type-label {
            font-size: 0.82rem;
            color: #444;
            min-width: 40px;
        }
        .ui-server-types {
            display: flex;
            gap: 4px;
        }
        .ui-server-btn {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.78rem;
            border: 1px solid #bbb;
            background: #f0f0f0;
            color: #555;
            cursor: default;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
        .ui-server-btn.active {
            background: #e8e8e8;
            border-color: #888;
            color: #222;
            font-weight: bold;
        }
        .ui-dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
        .ui-dot-y { background:#e6a817; }
        .ui-dot-g { background:#4caf50; }
        .ui-dot-b { background:#2196c4; }
        .ui-field {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 9px;
        }
        .ui-field label {
            font-size: 0.78rem;
            color: #555;
            min-width: 70px;
            text-align: right;
        }
        .ui-input {
            background: white;
            border: 1px solid #bbb;
            border-radius: 3px;
            padding: 4px 8px;
            color: #222;
            font-size: 0.78rem;
            width: 140px;
            display: inline-block;
        }
        .ui-input.highlight {
            border-color: #2196c4;
            box-shadow: 0 0 0 2px rgba(33,150,196,0.2);
        }
        .ui-input.short { width: 55px; }
        .ui-dropdown-field {
            background: white;
            border: 1px solid #bbb;
            border-radius: 3px;
            padding: 4px 8px;
            color: #222;
            font-size: 0.78rem;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            min-width: 180px;
        }
        .ui-dropdown-field span { flex:1; }
        .ui-btn-test {
            margin-top: 10px;
            background: #f0f0f0;
            color: #333;
            border: 1px solid #bbb;
            border-radius: 4px;
            padding: 5px 14px;
            font-size: 0.78rem;
            cursor: default;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
        .ui-connected {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            margin-top: 8px;
            font-size: 0.78rem;
            color: #2e7d32;
            font-weight: bold;
        }
        .ui-dropdown-list {
            background: white;
            border: 1px solid #bbb;
            border-radius: 3px;
            min-width: 200px;
            display: inline-block;
            margin-top: -4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.12);
        }
        .ui-dropdown-list div {
            padding: 5px 12px;
            font-size: 0.78rem;
            color: #333;
        }
        .ui-dropdown-list div.selected {
            background: #d0e8f5;
            color: #111;
        }
        .ui-arrow { font-size: 1.1rem; }
        .faq-item {
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            margin-bottom: 8px;
            overflow: hidden;
        }
        .faq-question {
            width: 100%;
            background: #f8f8f8;
            border: none;
            padding: 12px 16px;
            text-align: left;
            font-size: 0.92rem;
            font-weight: bold;
            color: #333;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background 0.2s;
            box-shadow: none;
            transform: none;
            border-radius: 0;
        }
        .faq-question:hover {
            background: #f0f0f0;
            transform: none;
            box-shadow: none;
        }
        .faq-answer {
            display: none;
            padding: 12px 16px;
            font-size: 0.88rem;
            color: #555;
            line-height: 1.6;
            border-top: 1px solid #e0e0e0;
            background: white;
        }
        .intro-box {
            background: #fff8e1;
            border-left: 5px solid #f7931a;
            border-radius: 10px;
            padding: 18px 20px;
            margin-bottom: 16px;
            color: #555;
            line-height: 1.7;
        }
        .courses-section {
            margin-bottom: 24px;
        }
        .courses-section h3 {
            color: #d35400;
            margin: 0 0 12px;
            font-size: 1.05rem;
        }
        .course-cards {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
        }
        .course-card {
            background: white;
            border: 2px solid rgba(247, 147, 26, 0.3);
            border-radius: 12px;
            padding: 16px;
            text-decoration: none;
            color: #333;
            transition: transform 0.2s, border-color 0.2s, box-shadow 0.2s;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .course-card:hover {
            transform: translateY(-3px);
            border-color: #f7931a;
            box-shadow: 0 6px 18px rgba(247,147,26,0.15);
            text-decoration: none;
            color: #333;
        }
        .course-card .course-num {
            font-size: 0.75rem;
            font-weight: bold;
            color: #f7931a;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .course-card .course-title {
            font-size: 0.95rem;
            font-weight: bold;
            color: #333;
            line-height: 1.4;
        }
        .course-card .course-desc {
            font-size: 0.82rem;
            color: #777;
            line-height: 1.5;
        }
        .ln-course-cards {
            grid-template-columns: repeat(2, 1fr);
        }
        .ln-course-card {
            border-color: rgba(33, 150, 196, 0.3);
        }
        .ln-course-card:hover {
            border-color: #2196c4;
            box-shadow: 0 6px 18px rgba(33,150,196,0.15);
        }
        @media (max-width: 600px) {
            .course-cards, .ln-course-cards {
                grid-template-columns: 1fr;
            }
        }
        @media (max-width: 768px) {
            .main-menu {
                grid-template-columns: 1fr;
            }
        }
        @media (max-width: 640px) {
            pre {
                font-size: 0.72rem;
                padding: 10px;
            }
            details summary {
                flex-wrap: wrap;
                gap: 6px;
            }
            details summary span[style*="margin-left:auto"] {
                margin-left: 0 !important;
            }
            textarea#ln_invoice {
                font-size: 0.75rem;
            }
            .section-wrapper {
                padding: 16px;
            }
            .container {
                padding: 16px;
            }
            h1 {
                font-size: 1.4rem;
            }
            h2 {
                font-size: 1.2rem;
            }
            .success-popup {
                padding: 24px 20px;
                min-width: unset;
                width: 90vw;
            }
        }
        .success-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.55);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            cursor: pointer;
            overflow: hidden;
        }
        .success-popup {
            background: white;
            border-radius: 24px;
            border: 3px solid #f7931a;
            padding: 40px 50px;
            text-align: center;
            position: relative;
            min-width: 300px;
            max-width: 420px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.4);
            animation: popup-in 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            z-index: 1001;
        }
        @keyframes popup-in {
            from { transform: scale(0.4); opacity: 0; }
            to   { transform: scale(1);   opacity: 1; }
        }
        .popup-emoji {
            font-size: 4rem;
            display: block;
            animation: bounce 0.6s ease infinite alternate;
        }
        @keyframes bounce {
            from { transform: translateY(0); }
            to   { transform: translateY(-10px); }
        }
        .popup-title {
            font-size: 1.7rem;
            font-weight: bold;
            color: #f7931a;
            margin: 14px 0 8px;
        }
        .popup-sub {
            color: #555;
            font-size: 0.95rem;
            line-height: 1.5;
        }
        .popup-close {
            margin-top: 18px;
            font-size: 0.82rem;
            color: #bbb;
        }
        .firework {
            position: fixed;
            pointer-events: none;
            z-index: 1002;
        }
        .spark {
            position: fixed;
            width: 7px;
            height: 7px;
            border-radius: 50%;
            pointer-events: none;
            z-index: 1002;
        }
        .os-tab-btn {
            background: #f0f0f0;
            color: #555;
            border: 2px solid #ddd;
            border-radius: 10px;
            padding: 12px 22px;
            font-size: 0.95rem;
            cursor: pointer;
            font-weight: bold;
            transition: background 0.2s, border-color 0.2s, color 0.2s;
            box-shadow: none;
            transform: none;
            min-width: 120px;
            min-height: 44px;
        }
        @media (max-width: 420px) {
            .os-tab-btn { flex: 1; min-width: 0; padding: 12px 8px; font-size: 0.88rem; }
        }
        .os-tab-btn:hover {
            background: #fff3e0;
            border-color: #f7931a;
            color: #f7931a;
            transform: none;
            box-shadow: none;
        }
        .os-tab-btn.active {
            background: #f7931a;
            color: white;
            border-color: #f7931a;
        }
        .addr-preview {
            font-size: 0.83rem;
            margin: -8px 0 10px;
            padding: 7px 12px;
            border-radius: 6px;
            display: none;
        }
        .addr-preview.ok  { background: #f0fff4; color: #2e7d32; border-left: 3px solid #4caf50; }
        .addr-preview.bad { background: #fff3cd; color: #856404; border-left: 3px solid #f7931a; }
        .os-warning {
            background: #fff3cd;
            border-left: 4px solid #f7931a;
            border-radius: 8px;
            padding: 12px 16px;
            margin: 10px 0;
            font-size: 0.9rem;
        }
        .zoomable { cursor: zoom-in; transition: opacity 0.15s; }
        .zoomable:hover { opacity: 0.9; }
        #lightbox {
            display: none; position: fixed; inset: 0; z-index: 9999;
            background: rgba(0,0,0,0.85); cursor: zoom-out;
            align-items: center; justify-content: center;
        }
        #lightbox.open { display: flex; }
        #lightbox img {
            max-width: 92vw; max-height: 92vh;
            border-radius: 8px; box-shadow: 0 8px 40px rgba(0,0,0,0.6);
            animation: lb-in 0.15s ease;
        }
        #lightbox-close {
            position: absolute; top: 16px; right: 22px;
            color: white; font-size: 2rem; font-weight: 300;
            cursor: pointer; line-height: 1; user-select: none;
        }
        @keyframes lb-in { from { transform: scale(0.92); opacity:0; } to { transform: scale(1); opacity:1; } }
    </style>
</head>
<body>
    <div class="container">
        <h1>Signet Faucet Hub</h1>
        <div id="homeIntro">
        <div class="intro-box">
            <strong>What is this?</strong> This is a Signet faucet — a tool that sends you free test Bitcoin so you can practise using it safely. Signet is a practice version of Bitcoin that works exactly the same way, but the coins have no real value. You can send, receive, and experiment freely without risking any money.
            <br><br>
            Amounts are often shown in <strong>sats</strong> (short for satoshis) — the smallest unit of Bitcoin. There are 100,000,000 sats in one Bitcoin, so 10,000 sats = 0.0001 BTC.
        </div>
        <div class="courses-section">
            <h3>&#127891; Free courses from PlanB Academy <span style="font-size:0.78rem; font-weight:400; color:#aaa; margin-left:6px;">— optional, explore at your own pace</span></h3>

            <div style="display:flex; align-items:center; gap:8px; margin:0 0 10px;">
                <span style="font-size:1rem;">&#8383;</span>
                <span style="font-weight:600; font-size:0.9rem; color:#555;">Bitcoin — Beginner</span>
                <span style="font-size:0.75rem; color:#f7931a; background:#fff3e0; padding:2px 8px; border-radius:10px; font-weight:600;">Start here</span>
            </div>
            <div class="course-cards" style="margin-bottom:20px;">
                <a class="course-card" href="https://planb.academy/en/courses/the-bitcoin-journey-2b7dc507-81e3-4b70-88e6-41ed44239966" target="_blank">
                    <span class="course-num">Course 1</span>
                    <span class="course-title">The Bitcoin Journey</span>
                    <span style="font-size:0.75rem; color:#bbb;">↗ opens in new tab</span>
                    <span class="course-desc">Understand what Bitcoin is, how it works, and why it matters — from scratch.</span>
                </a>
                <a class="course-card" href="https://planb.academy/en/courses/getting-your-first-bitcoins-f3e3843d-1a1d-450c-96d6-d7232158b81f" target="_blank">
                    <span class="course-num">Course 2</span>
                    <span class="course-title">Getting Your First Bitcoins</span>
                    <span style="font-size:0.75rem; color:#bbb;">↗ opens in new tab</span>
                    <span class="course-desc">Learn how to safely acquire and store Bitcoin for the first time.</span>
                </a>
                <a class="course-card" href="https://planb.academy/en/courses/setting-up-your-first-bitcoin-node-3cd9cb94-82e8-417a-9c5a-02afc2589426" target="_blank">
                    <span class="course-num">Course 3</span>
                    <span class="course-title">Setting Up Your First Bitcoin Node</span>
                    <span style="font-size:0.75rem; color:#bbb;">↗ opens in new tab</span>
                    <span class="course-desc">Run your own Bitcoin node — exactly what powers this faucet.</span>
                </a>
            </div>

            <div style="display:flex; align-items:center; gap:8px; margin:0 0 10px;">
                <span style="font-size:1rem;">&#9889;</span>
                <span style="font-weight:600; font-size:0.9rem; color:#555;">Lightning Network — Intermediate</span>
                <span style="font-size:0.75rem; color:#2196c4; background:#e3f2fd; padding:2px 8px; border-radius:10px; font-weight:600;">Next step</span>
            </div>
            <div class="course-cards ln-course-cards">
                <a class="course-card ln-course-card" href="https://planb.academy/en/courses/lightning-network-theory-34bd43ef-6683-4a5c-b239-7cb1e40a4aeb" target="_blank">
                    <span class="course-num" style="color:#2196c4;">Course 4</span>
                    <span class="course-title">Lightning Network Theory</span>
                    <span style="font-size:0.75rem; color:#bbb;">↗ opens in new tab</span>
                    <span class="course-desc">Understand how Lightning enables instant, near-free Bitcoin payments — payment channels, routing, HTLCs, and the network topology.</span>
                </a>
                <a class="course-card ln-course-card" href="https://planb.academy/en/courses/set-up-your-first-lightning-node-593e483e-1785-4e83-aa7e-32b99056844c" target="_blank">
                    <span class="course-num" style="color:#2196c4;">Course 5</span>
                    <span class="course-title">Set Up Your First Lightning Node</span>
                    <span style="font-size:0.75rem; color:#bbb;">↗ opens in new tab</span>
                    <span class="course-desc">Go hands-on — run your own Lightning node, open channels, and send your first Lightning payment on the network.</span>
                </a>
            </div>
        </div>
        <p>Ready to get started? Choose a network below. <span style="color:#888; font-size:0.9rem;">— Brand new? We recommend starting with <strong>Bitcoin</strong>. Lightning is more advanced and requires extra setup.</span></p>
        <div style="text-align:center; margin:0 0 8px; font-size:0.83rem; color:#aaa;">
            Stuck or have questions? <a href="https://planb.academy" target="_blank" style="color:#f7931a;">Visit PlanB Academy ↗</a> or ask your instructor directly.
        </div>
        </div>

        <div class="main-menu" id="homeMenu">
            <button class="menu-card" onclick="showSection('bitcoinSection')">
                <h2>Bitcoin Signet Faucet</h2>
                <div class="menu-icon">₿</div>
                <p>Request free test coins to your Signet wallet address.</p>
            </button>
            <button class="menu-card" onclick="showSection('lightningSection')">
                <h2>Lightning Signet Faucet</h2>
                <div class="menu-icon">⚡</div>
                <p>Request instant test sats via a Lightning payment. Requires a Lightning wallet and a channel to our node.</p>
                <p class="subtext">Intermediate — do Bitcoin first</p>
            </button>
        </div>

        <div class="section-wrapper hidden" id="bitcoinSection">
            <button class="back-button" onclick="showSection('homeMenu')">← Back</button>
            <h2>Bitcoin Signet Faucet</h2>
            <p>Do you already have a Signet wallet set up?</p>
            <div class="main-menu">
                <button class="menu-card" onclick="showSection('faucetSection')">
                    <h2>I have a wallet</h2>
                    <div class="menu-icon">👛</div>
                    <p>I already have a Signet wallet and just need test coins.</p>
                </button>
                <button class="menu-card" onclick="showSection('newUserSection')">
                    <h2>I'm new here</h2>
                    <div class="menu-icon">🚀</div>
                    <p>I need to set up a wallet and get started on Signet.</p>
                </button>
            </div>
        </div>

        <div class="section-wrapper hidden" id="lightningSection">
            <button class="back-button" onclick="showSection('homeMenu')">← Back</button>
            <h2>Lightning Signet Faucet</h2>
            <p>Do you already have a Lightning wallet that can receive on Signet?</p>
            <div class="main-menu">
                <button class="menu-card" onclick="showSection('lightningFaucetSection')">
                    <h2>I have a wallet</h2>
                    <div class="menu-icon">⚡</div>
                    <p>I can generate a Lightning invoice and receive test sats.</p>
                </button>
                <button class="menu-card" onclick="showSection('lightningNewUserSection')">
                    <h2>I'm new to Lightning</h2>
                    <div class="menu-icon">🚀</div>
                    <p>I need to set up a Lightning wallet first.</p>
                </button>
            </div>
        </div>

        <div class="section-wrapper hidden" id="lightningFaucetSection">
            <button class="back-button" onclick="showSection('lightningSection')">← Back</button>
            <h2>Lightning Signet Faucet</h2>
            <div id="lnd-status" style="font-size:0.82rem; color:#aaa; margin:-4px 0 14px;">⚡ Checking node status...</div>
            <p>Generate a Lightning invoice in your wallet and paste it below to receive up to <strong>10,000 test sats</strong> instantly.</p>
            <form method="post" action="/lightning" autocomplete="off">
                <label for="ln_invoice">Lightning Invoice (BOLT11):</label>
                <textarea id="ln_invoice" name="ln_invoice" placeholder="lnbcrt1..." rows="4" required style="width:100%; box-sizing:border-box; font-family:monospace; font-size:0.82rem; padding:10px 12px; border:1px solid #ddd; border-radius:6px; resize:vertical; margin-bottom:6px;" oninput="previewInvoice(this.value)"></textarea>
                <div id="invoicePreview" class="addr-preview" style="display:none; margin-bottom:8px;"></div>
                <p style="font-size:0.85rem; color:#777; margin:0 0 8px;">Your Signet invoice starts with <code>lnbcrt</code>. Set an amount between 1 and 10,000 sats in your wallet before generating it. <a href="#" onclick="showSection('lightningNewUserSection'); return false;" style="font-size:0.82rem;">Don't have a wallet yet?</a></p>
                <p style="font-size:0.85rem; color:#777; margin:0 0 14px;">⚡ The same wallet and channel you set up here will also receive your <strong>quiz rewards</strong> — sats paid out automatically when you answer correctly.</p>
                <p class="captcha-label">Type the code below to confirm you're human:</p>
                <div class="captcha-box">{{ ln_captcha_code }}</div>
                <input type="text" id="ln_captcha" name="ln_captcha" placeholder="Enter the 4-digit code" maxlength="4" required autocomplete="off">
                <button type="submit">Pay Invoice</button>
            </form>
            {% if ln_message %}
            <div class="message {{ 'success' if ln_success else 'error' }} ln-message">
                {{ ln_message }}
                {% if ln_hint %}
                <button class="hint-btn" onclick="toggleHint()">?</button>
                {% endif %}
            </div>
            {% if ln_hint %}
            <div class="hint-box" id="hintBox">{{ ln_hint }}</div>
            {% endif %}
            {% endif %}
            <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:14px 18px; margin:18px 0;">
                <strong>⚡ How Lightning payments work</strong>
                <ul style="margin:8px 0 0; padding-left:20px; background:none; border:none; color:#555;">
                    <li>Lightning payments are instant — no waiting for blocks</li>
                    <li>Your wallet needs to be online and connected to the Signet Lightning network</li>
                    <li>Invoices expire after a short time — generate a fresh one if payment fails</li>
                    <li>Max per request: <strong>10,000 sats</strong></li>
                </ul>
            </div>
            <div style="background:#f0fff4; border-left:4px solid #4caf50; border-radius:8px; padding:14px 18px; margin:16px 0;">
                <strong>🎉 What can you do with your test sats?</strong>
                <ul style="margin:8px 0 0; padding-left:20px; background:none; border:none; color:#555;">
                    <li>Send a Lightning payment back — open your wallet and pay an invoice from a friend</li>
                    <li>Try sending sats to a different Lightning wallet to feel how instant it is</li>
                    <li>Experiment with creating invoices of different amounts</li>
                    <li>These sats have no real value — make mistakes freely and learn how Lightning works</li>
                </ul>
            </div>
        </div>

        <div class="section-wrapper hidden" id="lightningNewUserSection">
            <button class="back-button" onclick="showSection('lightningSection')">← Back</button>
            <h2>New to Lightning Network?</h2>

            <!-- Plain-English explainers -->
            <div style="display:flex; flex-direction:column; gap:12px; margin:0 0 20px;">
                <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:14px 18px;">
                    <strong>⚡ What is Lightning?</strong>
                    <p style="margin:8px 0 0; color:#444; font-size:0.9rem;">Regular Bitcoin transactions go into blocks that take around 10 minutes to confirm. Lightning is a second layer built on top of Bitcoin that lets you send payments <em>instantly</em> — no block waiting, almost no fee. It is the same bitcoin, just moved through a faster lane.</p>
                </div>
                <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:14px 18px;">
                    <strong>🔗 What is a channel?</strong>
                    <p style="margin:8px 0 0; color:#444; font-size:0.9rem;">Think of it like a tab at a bar. Instead of paying for every drink individually, you open a tab (the channel), spend freely back and forth, and settle at the end. Opening and closing a channel is a real Bitcoin transaction — but everything in between is instant. To receive Lightning payments, your wallet needs an open channel to someone who is already on the network — in this case, our faucet hub.</p>
                </div>
            </div>

            <!-- Honest difficulty warning -->
            <div style="background:#fff3cd; border-left:4px solid #f7931a; border-radius:8px; padding:14px 18px; margin:0 0 20px;">
                <strong>⚠️ Heads up — this is not a beginner setup</strong>
                <p style="margin:8px 0 6px; color:#555; font-size:0.9rem;">Unlike the Bitcoin faucet where you just need an address, Lightning on Signet requires three things that are all a bit technical:</p>
                <ul style="margin:0; padding-left:20px; color:#555; font-size:0.9rem; line-height:1.8;">
                    <li>A <strong>Lightning node</strong> running on your computer or server (software that speaks the Lightning protocol)</li>
                    <li>An <strong>open channel</strong> from your node to our hub — you open it yourself using the hub connection string your instructor shares</li>
                    <li>A <strong>wallet app</strong> connected to that node so you can create invoices and see your balance</li>
                </ul>
                <p style="margin:10px 0 0; color:#555; font-size:0.9rem;">None of the popular mobile Lightning wallets (Phoenix, Breez, Wallet of Satoshi) support Signet — they only work on real Bitcoin. This is why the setup is more involved.</p>
            </div>

            <!-- Recommended path -->
            <div style="background:#f0fff4; border-left:4px solid #4caf50; border-radius:8px; padding:14px 18px; margin:0 0 24px;">
                <strong>✅ Recommended path if you are starting from scratch</strong>
                <ol style="margin:10px 0 0; padding-left:20px; color:#444; font-size:0.9rem; line-height:2;">
                    <li>Start with the <a href="#" onclick="showSection('bitcoinSection'); return false;" style="color:#4caf50;">Bitcoin Signet Faucet</a> first — get comfortable with basic on-chain transactions</li>
                    <li>Take <a href="https://planb.academy/en/courses/lightning-network-theory-34bd43ef-6683-4a5c-b239-7cb1e40a4aeb" target="_blank" style="color:#4caf50;">Course 4 — Lightning Network Theory ↗</a> so the concepts below make sense</li>
                    <li>Take <a href="https://planb.academy/en/courses/set-up-your-first-lightning-node-593e483e-1785-4e83-aa7e-32b99056844c" target="_blank" style="color:#4caf50;">Course 5 — Set Up Your First Lightning Node ↗</a> for hands-on node setup</li>
                    <li>Come back here, follow the wallet guide below, and ask your instructor to open a channel</li>
                    <li>Once your channel is active you can receive from this Lightning faucet — <strong>and earn sats from the quiz too</strong> ⚡ Quiz rewards are paid out automatically to your Lightning wallet when you answer correctly</li>
                </ol>
            </div>

            <!-- Glossary -->
            <h3 style="margin:0 0 10px;">Terms you will encounter</h3>
            <div style="display:flex; flex-direction:column; gap:8px; margin:0 0 24px;">
                <div style="background:#f8f9fa; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                    <strong>Lightning node</strong> — software (called LND or Core Lightning) that runs on your computer and connects to the Lightning Network. It holds your funds and handles payments.
                </div>
                <div style="background:#f8f9fa; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                    <strong>Node pubkey</strong> — your node's unique address on the Lightning Network, like a phone number. You share it with others so they can open a channel to you.
                </div>
                <div style="background:#f8f9fa; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                    <strong>Macaroon</strong> — a file that acts as an access key to your node, like a password. Wallet apps need it to connect and control the node on your behalf. Keep it private.
                </div>
                <div style="background:#f8f9fa; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                    <strong>REST port</strong> — a number (usually 8080) that tells the wallet app where to reach your node over the network. Think of it as a door number on the same building.
                </div>
                <div style="background:#f8f9fa; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                    <strong>Invoice (BOLT11)</strong> — a payment request you generate in your wallet. It encodes how many sats you want and where to send them. It starts with <code>lnbcrt</code> on Signet. Share it with the faucet and the payment arrives instantly.
                </div>
            </div>

            <!-- Wallet guides -->
            <h3 style="margin:0 0 10px;">Wallet options — pick one and follow the guide</h3>
            <p style="font-size:0.875rem; color:#666; margin:0 0 14px;">All three options below require you to already have an LND node running on Signet. If you do not have one yet, work through Course 5 first.</p>
            <div style="display:flex; flex-direction:column; gap:16px; margin:0 0 24px;">

                <!-- Zeus -->
                <details style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:0; overflow:hidden;">
                    <summary style="padding:16px 18px; cursor:pointer; list-style:none; display:flex; align-items:center; gap:10px; flex-wrap:wrap; user-select:none;">
                        <strong style="font-size:1rem;">Zeus Wallet</strong>
                        <span style="font-size:0.78rem; color:#888; background:#e8f5e9; padding:2px 7px; border-radius:10px;">Mobile — Android &amp; iOS</span>
                        <a href="https://zeusln.app" target="_blank" onclick="event.stopPropagation()" style="margin-left:auto; font-size:0.82rem; color:#f7931a; text-decoration:none; white-space:nowrap;">zeusln.app ↗</a>
                        <span style="font-size:0.82rem; color:#aaa; margin-left:6px;">▼ How to connect</span>
                    </summary>
                    <div style="padding:0 18px 16px; border-top:1px solid #e8e8e8;">
                        <a href="https://www.youtube.com/watch?v=hmmehTnV3ys" target="_blank" style="display:flex; align-items:center; gap:10px; background:#ff0000; color:white; border-radius:8px; padding:10px 14px; text-decoration:none; font-weight:600; font-size:0.9rem; margin:14px 0 14px;">
                            <span style="font-size:1.4rem;">▶</span> Watch the setup video on YouTube ↗
                        </a>
                        <p style="font-size:0.875rem; color:#555; margin:0 0 4px;">Zeus is a mobile app that acts as the front-end for your LND node. It does <em>not</em> run a node by itself on Signet — it connects to your existing one over the network.</p>
                        <p style="font-size:0.875rem; color:#555; margin:0 0 12px;"><strong>Before you start:</strong> make sure your LND node is running and reachable from your phone (same Wi-Fi network, or exposed via a domain/VPN).</p>
                        <ol style="padding-left:20px; color:#444; font-size:0.875rem; line-height:1.9; margin:0;">
                            <li>Download Zeus from the <a href="https://zeusln.app" target="_blank" style="color:#f7931a;">App Store or Google Play</a></li>
                            <li>Open the app and tap <strong>Connect a node</strong> on the welcome screen</li>
                            <li>Tap <strong>+ Add a new node</strong> and choose <strong>LND</strong> as the node type</li>
                            <li>Enter the <strong>Host</strong> — the IP address or domain name of the computer running your LND node</li>
                            <li>Set the <strong>REST port</strong> to <code>8080</code> (this is the default — the door number Zeus uses to talk to your node)</li>
                            <li>Get your <strong>macaroon</strong> (the access key): on your node computer, run this command and copy the output: <code>xxd -p -c 256 ~/.lnd-signet/data/chain/bitcoin/signet/admin.macaroon</code></li>
                            <li>Paste that long string of letters and numbers into the <strong>Admin Macaroon</strong> field in Zeus</li>
                            <li>Turn <strong>SSL off</strong> if you are on the same local network, or on if you are connecting over the internet with a domain</li>
                            <li>Tap <strong>Save node config</strong> — Zeus connects and shows your Signet balance</li>
                            <li>Tap <strong>Receive</strong>, enter an amount (1–10,000 sats), and copy the invoice starting with <code>lnbcrt</code></li>
                        </ol>
                    </div>
                </details>

                <!-- Alby -->
                <details style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:0; overflow:hidden;">
                    <summary style="padding:16px 18px; cursor:pointer; list-style:none; display:flex; align-items:center; gap:10px; flex-wrap:wrap; user-select:none;">
                        <strong style="font-size:1rem;">Alby</strong>
                        <span style="font-size:0.78rem; color:#888; background:#e3f2fd; padding:2px 7px; border-radius:10px;">Browser — Chrome &amp; Firefox</span>
                        <a href="https://getalby.com" target="_blank" onclick="event.stopPropagation()" style="margin-left:auto; font-size:0.82rem; color:#f7931a; text-decoration:none; white-space:nowrap;">getalby.com ↗</a>
                        <span style="font-size:0.82rem; color:#aaa; margin-left:6px;">▼ How to connect</span>
                    </summary>
                    <div style="padding:0 18px 16px; border-top:1px solid #e8e8e8;">
                        <a href="https://www.youtube.com/watch?v=2Z1BzwxdP4I" target="_blank" style="display:flex; align-items:center; gap:10px; background:#ff0000; color:white; border-radius:8px; padding:10px 14px; text-decoration:none; font-weight:600; font-size:0.9rem; margin:14px 0 14px;">
                            <span style="font-size:1.4rem;">▶</span> Watch the setup video on YouTube ↗
                        </a>
                        <p style="font-size:0.875rem; color:#555; margin:0 0 4px;">Alby is a browser extension that sits in your browser toolbar and connects to your LND node. Good choice if you prefer to work on a desktop computer rather than a phone.</p>
                        <p style="font-size:0.875rem; color:#555; margin:0 0 12px;"><strong>Before you start:</strong> your LND node must be running and reachable from the computer where the browser is installed.</p>
                        <ol style="padding-left:20px; color:#444; font-size:0.875rem; line-height:1.9; margin:0;">
                            <li>Install the Alby extension from <a href="https://getalby.com" target="_blank" style="color:#f7931a;">getalby.com</a> — click Add to Chrome or Add to Firefox</li>
                            <li>Click the Alby icon in your browser toolbar, then click <strong>Get started</strong></li>
                            <li>Choose <strong>Connect to your own wallet</strong> and select <strong>LND</strong> from the list</li>
                            <li>In the <strong>LND REST URL</strong> field, enter your node's address and REST port — for example <code>http://127.0.0.1:8080</code> if the node is on the same machine, or <code>https://yourdomain.com:8080</code> if it is remote</li>
                            <li>Get your <strong>macaroon</strong> (the access key) by running on your node: <code>xxd -p -c 256 ~/.lnd-signet/data/chain/bitcoin/signet/admin.macaroon</code> — paste the output into the <strong>Admin Macaroon</strong> field</li>
                            <li>Click <strong>Connect</strong> — Alby will show your Signet node balance in the toolbar</li>
                            <li>Click the Alby icon → <strong>Receive</strong> → enter an amount (1–10,000 sats) → copy the <code>lnbcrt...</code> invoice</li>
                        </ol>
                    </div>
                </details>

                <!-- LND + RTL / ThunderHub -->
                <details style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:0; overflow:hidden;">
                    <summary style="padding:16px 18px; cursor:pointer; list-style:none; display:flex; align-items:center; gap:10px; flex-wrap:wrap; user-select:none;">
                        <strong style="font-size:1rem;">LND + RTL / ThunderHub</strong>
                        <span style="font-size:0.78rem; color:#888; background:#fce4ec; padding:2px 7px; border-radius:10px;">Desktop — web dashboard</span>
                        <span style="margin-left:auto; display:flex; gap:10px;">
                            <a href="https://github.com/lightningnetwork/lnd" target="_blank" onclick="event.stopPropagation()" style="font-size:0.82rem; color:#f7931a; text-decoration:none; white-space:nowrap;">LND ↗</a>
                            <a href="https://github.com/Ride-The-Lightning/RTL" target="_blank" onclick="event.stopPropagation()" style="font-size:0.82rem; color:#f7931a; text-decoration:none; white-space:nowrap;">RTL ↗</a>
                            <a href="https://thunderhub.io" target="_blank" onclick="event.stopPropagation()" style="font-size:0.82rem; color:#f7931a; text-decoration:none; white-space:nowrap;">ThunderHub ↗</a>
                        </span>
                        <span style="font-size:0.82rem; color:#aaa; margin-left:6px;">▼ Step-by-step setup</span>
                    </summary>
                    <div style="padding:0 18px 20px; border-top:1px solid #e8e8e8;">
                        <a href="https://www.youtube.com/watch?v=KItleddMYFU" target="_blank" style="display:flex; align-items:center; gap:10px; background:#ff0000; color:white; border-radius:8px; padding:10px 14px; text-decoration:none; font-weight:600; font-size:0.9rem; margin:14px 0 14px;">
                            <span style="font-size:1.4rem;">▶</span> Watch the setup video on YouTube ↗
                        </a>
                        <p style="font-size:0.875rem; color:#555; margin:0 0 4px;">LND is the Lightning node software. RTL and ThunderHub are visual web dashboards you can open in a browser to manage it — no command line needed for day-to-day use. Follow the steps below one at a time.</p>
                        <p style="font-size:0.875rem; color:#555; margin:0 0 16px;"><strong>Before you start:</strong> your Bitcoin Signet node (Bitcoin Core) must already be running and synced. LND connects on top of it.</p>

                        <!-- Step 1 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 1 — Download LND</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">Go to <a href="https://github.com/lightningnetwork/lnd/releases" target="_blank" style="color:#f7931a;">github.com/lightningnetwork/lnd/releases</a> and download the zip file for your operating system (look for <strong>linux-amd64</strong>, <strong>darwin-amd64</strong> for Mac, or <strong>windows-amd64</strong>). Extract it — you'll get two programs: <code>lnd</code> and <code>lncli</code>. Move them somewhere in your PATH (e.g. <code>/usr/local/bin/</code>) so you can run them from any terminal.</p>
                        </div>

                        <!-- Step 2 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 2 — Create the config file</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">LND needs a config file to know it should use Signet and where to find your Bitcoin node. Create the file at <code>~/.lnd-signet/lnd.conf</code> with the content below. Replace <code>YOUR_RPC_USER</code> and <code>YOUR_RPC_PASS</code> with the username and password from your <code>bitcoin.conf</code> file.</p>
                            <div style="position:relative;">
                                <pre id="ln-lndconf" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0; white-space:pre;">[Bitcoin]
bitcoin.active=1
bitcoin.signet=1
bitcoin.node=bitcoind
bitcoin.signetchallenge=PASTE_FROM_INSTRUCTOR

[Bitcoind]
bitcoind.rpchost=127.0.0.1
bitcoind.rpcuser=YOUR_RPC_USER
bitcoind.rpcpass=YOUR_RPC_PASS
bitcoind.zmqpubrawblock=tcp://127.0.0.1:28332
bitcoind.zmqpubrawtx=tcp://127.0.0.1:28333

[Application Options]
lnddir=/home/YOUR_USERNAME/.lnd-signet
rpclisten=localhost:10010
restlisten=localhost:8080</pre>
                                <button onclick="copyToClipboard('ln-lndconf')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>

                        <!-- Step 3 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 3 — Start LND for the first time</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">Open a terminal and run the command below. LND will start and wait — it won't do anything until you create a wallet in the next step. Keep this terminal open.</p>
                            <div style="position:relative;">
                                <pre id="ln-start" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0;">lnd --lnddir=/home/YOUR_USERNAME/.lnd-signet</pre>
                                <button onclick="copyToClipboard('ln-start')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>

                        <!-- Step 4 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 4 — Create your Lightning wallet</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">Open a <strong>second</strong> terminal and run the command below. You will be asked to set a password (remember it — you need it every restart) and shown 24 seed words. <strong>Write those words down on paper and keep them safe.</strong> They are the backup for your entire wallet.</p>
                            <div style="position:relative;">
                                <pre id="ln-create" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 create</pre>
                                <button onclick="copyToClipboard('ln-create')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>

                        <!-- Step 5 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 5 — Add a shortcut (saves typing)</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">The full <code>lncli</code> command is long. Add this alias to your <code>~/.bashrc</code> file (run the line below, then open a new terminal). After that you can type <code>lns</code> instead of the full command every time.</p>
                            <div style="position:relative;">
                                <pre id="ln-alias" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0;">echo 'alias lns="lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010"' >> ~/.bashrc && source ~/.bashrc</pre>
                                <button onclick="copyToClipboard('ln-alias')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>

                        <!-- Step 6 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 6 — Get a funding address</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">LND has its own on-chain Bitcoin wallet separate from Bitcoin Core. You need to fund it before you can open a channel. Run the command below to get a deposit address (it will start with <code>tb1</code>).</p>
                            <div style="position:relative;">
                                <pre id="ln-newaddr" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 newaddress p2wkh</pre>
                                <button onclick="copyToClipboard('ln-newaddr')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                            <p style="font-size:0.82rem; color:#888; margin:8px 0 0;">Copy the address from the output, then go to the <a href="#" onclick="showSection('faucetSection'); return false;" style="color:#f7931a;">Bitcoin Signet Faucet</a> and request coins to that address. Ask your instructor to mine a block to confirm the deposit.</p>
                        </div>

                        <!-- Step 7 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 7 — Check your balance arrived</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">After a block is mined, run this to confirm your coins are there. You should see a number greater than 0 under <code>confirmed_balance</code>.</p>
                            <div style="position:relative;">
                                <pre id="ln-walbal" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 walletbalance</pre>
                                <button onclick="copyToClipboard('ln-walbal')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>

                        <!-- Step 8 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 8 — Connect to the hub</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">Your instructor will give you a connection string that looks like <code>PUBKEY@IP:9737</code>. This is the address of the Lightning hub node you will open a channel to. Run the command below, replacing <code>PASTE_HUB_CONNECTION_STRING</code> with what you received.</p>
                            <div style="position:relative;">
                                <pre id="ln-connect" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 connect PASTE_HUB_CONNECTION_STRING</pre>
                                <button onclick="copyToClipboard('ln-connect')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>

                        <!-- Step 9 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 9 — Open a channel</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">Now open a channel to the hub. This locks some of your on-chain sats into the channel so you can send and receive Lightning payments. Replace <code>PASTE_HUB_PUBKEY</code> with just the pubkey part (everything before the <code>@</code>).</p>
                            <div style="position:relative;">
                                <pre id="ln-openchan" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 openchannel --node_key=PASTE_HUB_PUBKEY --local_amt=50000</pre>
                                <button onclick="copyToClipboard('ln-openchan')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                            <p style="font-size:0.82rem; color:#888; margin:8px 0 0;">Ask your instructor to mine 6 blocks to confirm the channel. This usually takes just a few minutes on our Signet.</p>
                        </div>

                        <!-- Step 10 -->
                        <div style="margin:0 0 20px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 10 — Check your channel is active</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">Once 6 blocks are mined, check that the channel shows <code>"active": true</code>. If it still shows <code>false</code>, wait for one more block and try again.</p>
                            <div style="position:relative;">
                                <pre id="ln-listchan" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 listchannels</pre>
                                <button onclick="copyToClipboard('ln-listchan')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>

                        <!-- Step 11 -->
                        <div style="margin:0 0 8px;">
                            <p style="font-weight:600; margin:0 0 6px; font-size:0.9rem;">Step 11 — Create an invoice and get paid by the faucet</p>
                            <p style="font-size:0.85rem; color:#555; margin:0 0 8px;">You're ready! Generate an invoice for 1,000 sats. The output will contain a long string starting with <code>lnbcrt</code> — that is the invoice. Copy the value next to <code>"payment_request"</code>.</p>
                            <div style="position:relative;">
                                <pre id="ln-invoice" style="background:#1e1e1e; color:#d4d4d4; padding:12px 14px; border-radius:8px; font-size:0.78rem; overflow-x:auto; margin:0;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 addinvoice --amt=1000 --memo="faucet test"</pre>
                                <button onclick="copyToClipboard('ln-invoice')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                            <p style="font-size:0.82rem; color:#888; margin:8px 0 0;">Then <a href="#" onclick="showSection('lightningFaucetSection'); return false;" style="color:#f7931a;">go to the Lightning faucet →</a> and paste the invoice. Payment arrives in seconds.</p>
                        </div>
                    </div>
                </details>

            </div>

            <!-- Quick reference cheat sheet -->
            <details style="background:#1e1e1e; border-radius:10px; padding:0; overflow:hidden; margin:0 0 20px;">
                <summary style="padding:14px 18px; cursor:pointer; list-style:none; color:#d4d4d4; font-size:0.9rem; user-select:none; display:flex; align-items:center; gap:8px;">
                    <span style="color:#f7931a;">$</span> <strong>lncli quick reference</strong> <span style="font-size:0.8rem; color:#888; margin-left:auto;">▼ expand</span>
                </summary>
                <div style="padding:0 18px 16px; border-top:1px solid #333;">
                    <p style="color:#aaa; font-size:0.8rem; margin:10px 0 12px;">Handy commands — click <strong style="color:#f7931a;">Copy</strong> on any line to grab it.</p>
                    <div style="display:flex; flex-direction:column; gap:10px;">
                        <div>
                            <p style="color:#aaa; font-size:0.75rem; margin:0 0 4px;">Check node info and sync status</p>
                            <div style="position:relative;">
                                <pre id="qr-getinfo" style="background:#111; color:#d4d4d4; padding:10px 12px; border-radius:6px; font-size:0.78rem; margin:0; overflow-x:auto;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 getinfo</pre>
                                <button onclick="copyToClipboard('qr-getinfo')" style="position:absolute; top:5px; right:6px; background:#f7931a; color:white; border:none; padding:3px 7px; border-radius:3px; cursor:pointer; font-size:0.75rem;">Copy</button>
                            </div>
                        </div>
                        <div>
                            <p style="color:#aaa; font-size:0.75rem; margin:0 0 4px;">On-chain balance (sats available to open channels)</p>
                            <div style="position:relative;">
                                <pre id="qr-walbal" style="background:#111; color:#d4d4d4; padding:10px 12px; border-radius:6px; font-size:0.78rem; margin:0; overflow-x:auto;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 walletbalance</pre>
                                <button onclick="copyToClipboard('qr-walbal')" style="position:absolute; top:5px; right:6px; background:#f7931a; color:white; border:none; padding:3px 7px; border-radius:3px; cursor:pointer; font-size:0.75rem;">Copy</button>
                            </div>
                        </div>
                        <div>
                            <p style="color:#aaa; font-size:0.75rem; margin:0 0 4px;">Lightning balance (sats inside channels, ready to send/receive)</p>
                            <div style="position:relative;">
                                <pre id="qr-chanbal" style="background:#111; color:#d4d4d4; padding:10px 12px; border-radius:6px; font-size:0.78rem; margin:0; overflow-x:auto;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 channelbalance</pre>
                                <button onclick="copyToClipboard('qr-chanbal')" style="position:absolute; top:5px; right:6px; background:#f7931a; color:white; border:none; padding:3px 7px; border-radius:3px; cursor:pointer; font-size:0.75rem;">Copy</button>
                            </div>
                        </div>
                        <div>
                            <p style="color:#aaa; font-size:0.75rem; margin:0 0 4px;">List channels — check for "active": true</p>
                            <div style="position:relative;">
                                <pre id="qr-listchan" style="background:#111; color:#d4d4d4; padding:10px 12px; border-radius:6px; font-size:0.78rem; margin:0; overflow-x:auto;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 listchannels</pre>
                                <button onclick="copyToClipboard('qr-listchan')" style="position:absolute; top:5px; right:6px; background:#f7931a; color:white; border:none; padding:3px 7px; border-radius:3px; cursor:pointer; font-size:0.75rem;">Copy</button>
                            </div>
                        </div>
                        <div>
                            <p style="color:#aaa; font-size:0.75rem; margin:0 0 4px;">Create an invoice for 1,000 sats</p>
                            <div style="position:relative;">
                                <pre id="qr-addinvoice" style="background:#111; color:#d4d4d4; padding:10px 12px; border-radius:6px; font-size:0.78rem; margin:0; overflow-x:auto;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 addinvoice --amt=1000 --memo="test"</pre>
                                <button onclick="copyToClipboard('qr-addinvoice')" style="position:absolute; top:5px; right:6px; background:#f7931a; color:white; border:none; padding:3px 7px; border-radius:3px; cursor:pointer; font-size:0.75rem;">Copy</button>
                            </div>
                        </div>
                        <div>
                            <p style="color:#aaa; font-size:0.75rem; margin:0 0 4px;">Unlock wallet after a restart</p>
                            <div style="position:relative;">
                                <pre id="qr-unlock" style="background:#111; color:#d4d4d4; padding:10px 12px; border-radius:6px; font-size:0.78rem; margin:0; overflow-x:auto;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 unlock</pre>
                                <button onclick="copyToClipboard('qr-unlock')" style="position:absolute; top:5px; right:6px; background:#f7931a; color:white; border:none; padding:3px 7px; border-radius:3px; cursor:pointer; font-size:0.75rem;">Copy</button>
                            </div>
                        </div>
                        <div>
                            <p style="color:#aaa; font-size:0.75rem; margin:0 0 4px;">Decode a BOLT11 invoice — see what's inside before paying</p>
                            <div style="position:relative;">
                                <pre id="qr-decode" style="background:#111; color:#d4d4d4; padding:10px 12px; border-radius:6px; font-size:0.78rem; margin:0; overflow-x:auto;">lncli --lnddir=/home/YOUR_USERNAME/.lnd-signet --rpcserver=localhost:10010 decodepayreq &lt;BOLT11&gt;</pre>
                                <button onclick="copyToClipboard('qr-decode')" style="position:absolute; top:5px; right:6px; background:#f7931a; color:white; border:none; padding:3px 7px; border-radius:3px; cursor:pointer; font-size:0.75rem;">Copy</button>
                            </div>
                        </div>
                    </div>
                </div>
            </details>

            <!-- FAQ -->
            <details style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:0; overflow:hidden; margin:0 0 20px;">
                <summary style="padding:16px 18px; cursor:pointer; list-style:none; display:flex; align-items:center; gap:8px; user-select:none; font-weight:600;">
                    ❓ Frequently asked questions <span style="font-size:0.82rem; color:#aaa; margin-left:auto; font-weight:400;">▼ expand</span>
                </summary>
                <div style="padding:0 18px 16px; border-top:1px solid #e8e8e8; display:flex; flex-direction:column; gap:0; margin-top:12px;">

                    <details style="border-bottom:1px solid #eee; padding:0 0 4px;">
                        <summary style="cursor:pointer; padding:10px 4px; font-size:0.875rem; font-weight:600; list-style:none; user-select:none;">Why do I need to fund a separate LND wallet? I already have Bitcoin in Bitcoin Core.</summary>
                        <p style="font-size:0.85rem; color:#555; margin:8px 4px 12px;">LND manages its own on-chain wallet completely separately from Bitcoin Core. Think of Bitcoin Core as your bank account and LND as a prepaid card — you load the card before you can spend on Lightning. They don't share funds.</p>
                    </details>

                    <details style="border-bottom:1px solid #eee; padding:0 0 4px;">
                        <summary style="cursor:pointer; padding:10px 4px; font-size:0.875rem; font-weight:600; list-style:none; user-select:none;">What is the difference between walletbalance and channelbalance?</summary>
                        <p style="font-size:0.85rem; color:#555; margin:8px 4px 12px;"><code>walletbalance</code> shows your <em>on-chain</em> funds — sats sitting in the LND wallet, not yet in any channel. <code>channelbalance</code> shows sats that are <em>inside open channels</em> — those are the ones you can send or receive instantly over Lightning. Until you open a channel, channelbalance will be 0.</p>
                    </details>

                    <details style="border-bottom:1px solid #eee; padding:0 0 4px;">
                        <summary style="cursor:pointer; padding:10px 4px; font-size:0.875rem; font-weight:600; list-style:none; user-select:none;">How long does opening a channel take?</summary>
                        <p style="font-size:0.85rem; color:#555; margin:8px 4px 12px;">Opening a channel is a real Bitcoin transaction, so it needs block confirmations. On our custom Signet your instructor controls the miner, so it can be near-instant — just ask them to mine a few blocks. Typically you need 6 confirmations before the channel becomes active.</p>
                    </details>

                    <details style="border-bottom:1px solid #eee; padding:0 0 4px;">
                        <summary style="cursor:pointer; padding:10px 4px; font-size:0.875rem; font-weight:600; list-style:none; user-select:none;">Why can't I use Phoenix, Breez, or other mobile Lightning wallets?</summary>
                        <p style="font-size:0.85rem; color:#555; margin:8px 4px 12px;">Phoenix, Breez, Muun, and Wallet of Satoshi are built for mainnet Bitcoin only. They have the mainnet Lightning network hardcoded in — there is no way to point them at a custom Signet. That is why we use LND with Zeus or Alby: they let you connect to your own node, which can run on any network including our Signet.</p>
                    </details>

                    <details style="border-bottom:1px solid #eee; padding:0 0 4px;">
                        <summary style="cursor:pointer; padding:10px 4px; font-size:0.875rem; font-weight:600; list-style:none; user-select:none;">LND asks for a password every time I restart — is that normal?</summary>
                        <p style="font-size:0.85rem; color:#555; margin:8px 4px 12px;">Yes, completely normal. LND encrypts your wallet at rest and locks it when the process stops. You need to run <code>lncli ... unlock</code> after every restart and type your wallet password. This is a security feature — without the unlock step your funds cannot be moved even if someone gains access to the server.</p>
                    </details>

                    <details style="border-bottom:1px solid #eee; padding:0 0 4px;">
                        <summary style="cursor:pointer; padding:10px 4px; font-size:0.875rem; font-weight:600; list-style:none; user-select:none;">What is the minimum amount I can put in a channel?</summary>
                        <p style="font-size:0.85rem; color:#555; margin:8px 4px 12px;">LND requires a minimum of 20,000 sats per channel by default. We recommend 50,000 sats to give you enough room to open the channel (which costs a small fee), send a few payments, and still have inbound capacity left to receive from the faucet.</p>
                    </details>

                    <details style="border-bottom:1px solid #eee; padding:0 0 4px;">
                        <summary style="cursor:pointer; padding:10px 4px; font-size:0.875rem; font-weight:600; list-style:none; user-select:none;">Payment fails with "no route found" — what should I do?</summary>
                        <p style="font-size:0.85rem; color:#555; margin:8px 4px 12px;">First check your channel is active: run <code>listchannels</code> and look for <code>"active": true</code>. If the channel shows as pending, more blocks need to be mined. If it's active, the problem might be that the hub doesn't have enough balance on your side to push sats to you — ask your instructor to rebalance or open a channel from the hub to you.</p>
                    </details>

                    <details style="border-bottom:1px solid #eee; padding:0 0 4px;">
                        <summary style="cursor:pointer; padding:10px 4px; font-size:0.875rem; font-weight:600; list-style:none; user-select:none;">My invoice starts with lnbc instead of lnbcrt — why is it rejected?</summary>
                        <p style="font-size:0.85rem; color:#555; margin:8px 4px 12px;"><code>lnbc</code> = mainnet Lightning. <code>lnbcrt</code> = Signet/Regtest Lightning. If your invoice starts with <code>lnbc</code>, your LND is configured for mainnet, not our Signet. Check your <code>lnd.conf</code> — you need <code>bitcoin.signet=1</code> and <code>bitcoin.active=1</code> with the correct <code>signetchallenge</code> value from your instructor.</p>
                    </details>

                    <details style="padding:0 0 4px;">
                        <summary style="cursor:pointer; padding:10px 4px; font-size:0.875rem; font-weight:600; list-style:none; user-select:none;">I opened a channel but I can't receive — why?</summary>
                        <p style="font-size:0.85rem; color:#555; margin:8px 4px 12px;">When <em>you</em> open a channel, all the sats start on your side (called outbound capacity). To <em>receive</em> payments, the other side (the hub) needs to push sats towards you — this is called inbound capacity. The faucet does exactly this: it pays you sats into your channel, which gives you inbound capacity. After the first faucet payment, you'll have both outbound and inbound capacity available.</p>
                    </details>

                </div>
            </details>

            <!-- Troubleshooting -->
            <details style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:0; overflow:hidden; margin:20px 0 0;">
                <summary style="padding:16px 18px; cursor:pointer; list-style:none; display:flex; align-items:center; gap:8px; user-select:none; font-weight:600;">
                    🔧 Troubleshooting <span style="font-size:0.82rem; color:#aaa; margin-left:auto; font-weight:400;">▼ expand</span>
                </summary>
                <div style="padding:0 18px 16px; border-top:1px solid #e8e8e8; display:flex; flex-direction:column; gap:12px; margin-top:12px;">
                    <div style="background:#fff; border:1px solid #e8e8e8; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                        <strong>LND wallet locked after restart</strong>
                        <p style="margin:4px 0 0; color:#555;">LND locks its wallet every time it restarts. Unlock it before doing anything: <code>lncli --lnddir=~/.lnd-signet --rpcserver=localhost:10010 unlock</code></p>
                    </div>
                    <div style="background:#fff; border:1px solid #e8e8e8; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                        <strong>"chain backend is still syncing"</strong>
                        <p style="margin:4px 0 0; color:#555;">LND won't start until Bitcoin Core has fully synced. Check: <code>bitcoin-cli -signet getblockchaininfo | jq '.verificationprogress'</code> — should be very close to 1.0. On our custom Signet the chain is tiny so this is usually instant.</p>
                    </div>
                    <div style="background:#fff; border:1px solid #e8e8e8; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                        <strong>Can't connect to the hub node</strong>
                        <p style="margin:4px 0 0; color:#555;">Double-check the pubkey and IP — copy-paste directly from the instructor, no spaces or line breaks. Make sure port <code>9737</code> is not blocked by your firewall. Verify LND is running: <code>lncli --lnddir=~/.lnd-signet --rpcserver=localhost:10010 getinfo</code></p>
                    </div>
                    <div style="background:#fff; border:1px solid #e8e8e8; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                        <strong>Channel open fails</strong>
                        <p style="margin:4px 0 0; color:#555;">Check you have enough <em>confirmed</em> on-chain balance: <code>walletbalance</code> — the <code>confirmed_balance</code> must be higher than <code>--local_amt</code>. If funds are unconfirmed, ask the instructor to mine a block. Try a smaller amount: <code>--local_amt=20000</code></p>
                    </div>
                    <div style="background:#fff; border:1px solid #e8e8e8; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                        <strong>Payment fails — "no route found"</strong>
                        <p style="margin:4px 0 0; color:#555;">Check your channel is active: <code>listchannels</code> → <code>"active": true</code>. If the channel is pending, the instructor needs to mine more blocks to confirm it. Also check the hub has enough balance on its side to route to you — ask the instructor to rebalance if needed.</p>
                    </div>
                    <div style="background:#fff; border:1px solid #e8e8e8; border-radius:8px; padding:12px 16px; font-size:0.875rem;">
                        <strong>Invoice rejected — "does not start with lnbcrt"</strong>
                        <p style="margin:4px 0 0; color:#555;">Your LND must be configured with the correct <code>signetchallenge</code> for this Signet. If it is on a different network your invoices will start with a different prefix and won't be accepted. Check your LND startup flags or <code>lnd.conf</code>.</p>
                    </div>
                </div>
            </details>
        </div>

        <div class="section-wrapper hidden" id="faucetSection">
            <button class="back-button" onclick="showSection('bitcoinSection')">← Back</button>
            <p>Enter your Signet address below to receive 0.001 test BTC.</p>
            <form method="post" autocomplete="off">
                <label for="address">Signet Address:</label>
                <input type="text" id="address" name="address" placeholder="tb1..." required autocomplete="off" oninput="previewAddress(this.value)">
                <div id="addrPreview" class="addr-preview"></div>
                <p style="font-size:0.85rem; color:#777; margin:-4px 0 12px;">Find this in the <strong>Receive</strong> tab of your wallet. It starts with <code>tb1</code>, for example: <code>tb1q3x7k2m...</code> &nbsp;<a href="#" onclick="showSection('newUserSection'); return false;" style="font-size:0.82rem;">Don't have a wallet yet?</a></p>
                <p class="captcha-label">Type the code below to confirm you're human:</p>
                <div class="captcha-box">{{ captcha_code }}</div>
                <input type="text" id="captcha" name="captcha" placeholder="Enter the 4-digit code" maxlength="4" required autocomplete="off">
                <button type="submit">Get Coins</button>
            </form>
            {% if message %}
            <div class="message {{ 'success' if success else 'error' }}">
                {{ message | safe }}
                {% if hint %}
                <button class="hint-btn" onclick="toggleHint()">?</button>
                {% endif %}
            </div>
            {% if hint %}
            <div class="hint-box" id="hintBox">{{ hint }}</div>
            {% endif %}
            {% endif %}
            <div style="background:#fff; border:1px solid #e0e0e0; border-radius:10px; padding:18px 20px; margin:18px 0;">
                <p style="margin:0 0 12px; font-weight:600; font-size:1rem;">&#128269; Check your transaction</p>
                <p style="margin:0 0 14px; font-size:0.88rem; color:#555;">Once sent, paste your Transaction ID (TXID) into one of these explorers to watch it confirm in real time:</p>
                <div style="display:flex; gap:12px; flex-wrap:wrap;">
                    <a href="https://mempool-signet.planb.academy" target="_blank" style="flex:1; min-width:180px; display:flex; align-items:center; gap:10px; background:#1d1d1d; color:#fff; border-radius:8px; padding:12px 16px; text-decoration:none; font-weight:500; font-size:0.9rem;">
                        <span style="font-size:1.4rem;">⚡</span>
                        <span>
                            <span style="display:block; font-size:0.75rem; color:#aaa; font-weight:400;">PlanB Signet</span>
                            Mempool Explorer
                        </span>
                        <span style="margin-left:auto; color:#888; font-size:0.8rem;">↗</span>
                    </a>
                    <a href="https://mempool.space/signet/" target="_blank" style="flex:1; min-width:180px; display:flex; align-items:center; gap:10px; background:#f8f8f8; color:#222; border:1px solid #ddd; border-radius:8px; padding:12px 16px; text-decoration:none; font-weight:500; font-size:0.9rem;">
                        <span style="font-size:1.4rem;">🔍</span>
                        <span>
                            <span style="display:block; font-size:0.75rem; color:#888; font-weight:400;">Public Signet</span>
                            mempool.space
                        </span>
                        <span style="margin-left:auto; color:#888; font-size:0.8rem;">↗</span>
                    </a>
                </div>
                <p style="margin:10px 0 0; font-size:0.78rem; color:#888;">&#9432;&nbsp; The PlanB explorer shows our custom Signet chain. mempool.space shows the public Signet — use it only if your wallet connects to the public network.</p>
            </div>
            <div style="background:#f0fff4; border-left:4px solid #4caf50; border-radius:8px; padding:14px 18px; margin:16px 0;">
                <strong>&#127381; What can you do with your test coins?</strong>
                <ul style="margin:8px 0 0; padding-left:20px; background:none; border:none; color:#555;">
                    <li>Send them to a friend's Signet address to practise a real Bitcoin transaction</li>
                    <li>Try sending a small amount back to a different address in your own wallet</li>
                    <li>Paste the Transaction ID into the Signet Explorer to watch it confirm in real time</li>
                    <li>These coins have no real value — experiment freely and make mistakes safely</li>
                </ul>
            </div>
        </div>
        <div class="section-wrapper hidden" id="newUserSection">
            <button class="back-button" onclick="showSection('bitcoinSection')">← Back</button>
            <h2>Choose your setup path</h2>
            <p>Both options get you a working Signet wallet. Pick the one that suits you best.</p>
            <div class="main-menu">
                <button class="menu-card" onclick="showSection('sparrowSection')">
                    <h2>Easy — Sparrow Wallet</h2>
                    <div class="menu-icon">🪟</div>
                    <p>A beginner-friendly desktop wallet with a visual interface. No command line needed.</p>
                    <p class="subtext">Recommended for most people</p>
                </button>
                <button class="menu-card" onclick="showSection('guideSection')">
                    <h2>Advanced — Bitcoin Core</h2>
                    <div class="menu-icon">⌨️</div>
                    <p>Run the full Bitcoin node directly. Uses the command line. More control, more to learn.</p>
                    <p class="subtext">For those who want to go deeper</p>
                </button>
            </div>
        </div>

        <div class="section-wrapper hidden" id="sparrowSection">
            <button class="back-button" onclick="showSection('newUserSection')">← Back</button>
            <h2>Easy Setup — Sparrow Wallet</h2>

            <p>Sparrow is a free desktop Bitcoin wallet with a clear visual interface — no command line needed. We will walk you through two things: installing Sparrow, and then switching it to practice mode (Signet) so you can use this faucet safely.</p>

            <div style="background:#fff8e1; border-left:4px solid #f7931a; border-radius:8px; padding:14px 18px; margin:16px 0;">
                <strong>Before you start — what do you need?</strong>
                <ul style="margin:8px 0 0; padding-left:20px; background:none; border:none;">
                    <li>A computer running Windows, macOS, or Linux</li>
                    <li>An internet connection</li>
                    <li>About 15 minutes</li>
                </ul>
            </div>

            <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:14px 18px; margin:16px 0;">
                <strong>What is a node?</strong>
                <p style="margin:8px 0 0;">A Bitcoin node is like a connection to the Bitcoin network — it checks transactions and keeps everything in sync. Sparrow needs to connect to one to work. Don't worry, you don't need to run your own for now. We will connect Sparrow to a trusted node from a list, which is fine for practice.</p>
            </div>

            <div style="margin:24px 0 10px;">
                <p style="margin-bottom:10px; font-weight:bold;">&#128187; Select your operating system — the guide will update automatically:</p>
                <div style="display:flex; gap:10px; flex-wrap:wrap;">
                    <button class="os-tab-btn" data-os="windows" onclick="selectOS('windows')">🪟 Windows</button>
                    <button class="os-tab-btn" data-os="macos" onclick="selectOS('macos')">🍎 macOS</button>
                    <button class="os-tab-btn" data-os="linux" onclick="selectOS('linux')">🐧 Linux</button>
                </div>
            </div>

            <p style="margin-top:20px;"><strong>Step 1 — Download and install Sparrow Wallet</strong></p>
            <p>Go to <a href="https://sparrowwallet.com/download/" target="_blank">sparrowwallet.com/download ↗</a> and download the version for your system. Follow the pictures below — they show exactly what you will see at every stage, including what to click and where to find things.</p>

            <!-- ===== WINDOWS ===== -->
            <div data-os="windows" style="margin-top:12px;">
                <div class="ui-guide">

                    <div class="ui-step">
                        <div class="ui-step-label">1</div>
                        <div class="ui-step-content">
                            <p><strong>Open your browser and go to the Sparrow download page.</strong><br>
                            Type <strong>sparrowwallet.com/download</strong> in the address bar at the top of your browser and press Enter. Scroll down until you see the Windows section, then click the <strong>Windows Installer</strong> download link.</p>
                            <img class="zoomable" src="/static/sparrow-download-page.png" alt="Sparrow download page" style="width:100%; max-width:520px; border-radius:8px; border:1px solid #ddd; display:block; margin:10px 0;">
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">2</div>
                        <div class="ui-step-content">
                            <p><strong>Find the downloaded file.</strong><br>
                            After downloading, your browser shows a bar at the bottom of the screen (Chrome/Edge) or a downloads icon at the top right (Firefox). Click <strong>Open file</strong> to run it straight away — or open <strong>File Explorer</strong>, click <strong>Downloads</strong> on the left, and double-click the file there.</p>
                            <div style="display:flex; gap:12px; flex-wrap:wrap; margin:10px 0; align-items:flex-start;">
                                <div>
                                    <div style="font-size:0.78rem; color:#888; margin-bottom:5px;">Browser download bar (bottom of screen):</div>
                                    <div class="ui-mockup" style="max-width:360px;">
                                        <div style="background:#323639; padding:8px 14px; display:flex; align-items:center; justify-content:space-between; gap:10px;">
                                            <div style="display:flex; align-items:center; gap:8px;">
                                                <img src="/static/sparrow-logo.png" alt="Sparrow" style="width:20px; height:20px;">
                                                <div>
                                                    <div style="color:white; font-size:0.8rem; font-weight:600;">Sparrow-2.1.0.exe</div>
                                                    <div style="color:#aaa; font-size:0.72rem;">Download complete</div>
                                                </div>
                                            </div>
                                            <button class="mock-btn" style="background:#1e88cf; color:white; font-size:0.78rem; white-space:nowrap;">Open file</button>
                                        </div>
                                    </div>
                                </div>
                                <div>
                                    <div style="font-size:0.78rem; color:#888; margin-bottom:5px;">Or find it in File Explorer → Downloads:</div>
                                    <div class="ui-mockup" style="max-width:280px;">
                                        <div class="mock-titlebar-win">
                                            <span>📁 Downloads</span>
                                            <span style="font-size:0.72rem; opacity:0.8;">─ □ ✕</span>
                                        </div>
                                        <div class="mock-body" style="padding:10px 12px; display:flex; flex-direction:column; gap:4px;">
                                            <div style="display:flex; align-items:center; gap:8px; padding:5px 8px; border-radius:3px; opacity:0.4; font-size:0.82rem;">📄 example-file.pdf</div>
                                            <div style="display:flex; align-items:center; gap:8px; padding:5px 8px; border-radius:3px; background:#cce4ff; font-size:0.82rem; font-weight:600; border:1px solid #7ab3e0;">
                                                <img src="/static/sparrow-logo.png" alt="" style="width:18px; height:18px;"> Sparrow-2.1.0.exe
                                            </div>
                                            <div style="display:flex; align-items:center; gap:8px; padding:5px 8px; border-radius:3px; opacity:0.4; font-size:0.82rem;">📄 document.docx</div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">3</div>
                        <div class="ui-step-content">
                            <p><strong>Run the installer.</strong><br>
                            Double-click the <code>.exe</code> file. A setup wizard opens. Click <strong>Next</strong> on each screen — all default settings are fine. Click <strong>Install</strong> on the final screen and wait for it to finish, then click <strong>Finish</strong>.</p>
                            <div class="ui-mockup" style="max-width:340px;">
                                <div class="mock-titlebar-win" style="background:#131415;">
                                    <div style="display:flex; align-items:center; gap:6px;">
                                        <img src="/static/sparrow-logo.png" alt="" style="width:16px; height:16px;">
                                        <span>Sparrow Wallet Setup</span>
                                    </div>
                                    <span style="font-size:0.72rem; opacity:0.8;">─ □ ✕</span>
                                </div>
                                <div class="mock-body" style="text-align:center; padding:22px 20px 16px;">
                                    <img src="/static/sparrow-logo.png" alt="Sparrow" style="width:52px; height:52px; margin-bottom:10px;">
                                    <div style="font-size:0.95rem; font-weight:600; margin-bottom:6px;">Welcome to Sparrow Wallet Setup</div>
                                    <p style="color:#555; font-size:0.82rem; margin:0 0 20px;">This will install Sparrow Wallet on your computer.<br>Click <strong>Next</strong> to continue.</p>
                                    <div style="display:flex; justify-content:flex-end; gap:8px;">
                                        <button class="mock-btn mock-btn-grey">Cancel</button>
                                        <button class="mock-btn" style="background:#1e88cf; color:white;">Next →</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">4</div>
                        <div class="ui-step-content">
                            <p><strong>Windows SmartScreen warning — do not panic!</strong><br>
                            Windows may show a blue warning saying it does not recognise Sparrow. This is completely normal for open-source apps that are not from big companies. You need to click <strong>More info</strong> first — this reveals a second button. Then click <strong>Run anyway</strong>.</p>
                            <div style="display:flex; gap:10px; flex-wrap:wrap; margin:10px 0; align-items:center;">
                                <div>
                                    <div style="font-size:0.75rem; color:#888; margin-bottom:5px;">① Click "More info":</div>
                                    <div class="ui-mockup" style="max-width:260px;">
                                        <div class="mock-titlebar-win" style="background:#1a4480;">
                                            <span style="font-size:0.8rem;">Windows protected your PC</span>
                                            <span style="font-size:0.72rem; opacity:0.8;">✕</span>
                                        </div>
                                        <div class="mock-body" style="background:#e8f0fb; padding:16px;">
                                            <div style="font-size:0.85rem; font-weight:600; color:#1a4480; margin-bottom:8px;">⛨ Windows Defender SmartScreen</div>
                                            <p style="font-size:0.8rem; color:#333; margin:0 0 8px;">Windows protected your PC. The app is not recognised.</p>
                                            <div style="color:#1565C0; font-size:0.82rem; font-weight:600; cursor:default; margin-bottom:14px;">▶ More info  ← click this</div>
                                            <div style="display:flex; justify-content:flex-end;">
                                                <button class="mock-btn mock-btn-grey">Don't run</button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div style="font-size:1.6rem; color:#bbb; flex:none; align-self:center; margin-top:18px;">→</div>
                                <div>
                                    <div style="font-size:0.75rem; color:#888; margin-bottom:5px;">② Click "Run anyway":</div>
                                    <div class="ui-mockup" style="max-width:260px;">
                                        <div class="mock-titlebar-win" style="background:#1a4480;">
                                            <span style="font-size:0.8rem;">Windows protected your PC</span>
                                            <span style="font-size:0.72rem; opacity:0.8;">✕</span>
                                        </div>
                                        <div class="mock-body" style="background:#e8f0fb; padding:16px;">
                                            <div style="font-size:0.85rem; font-weight:600; color:#1a4480; margin-bottom:6px;">⛨ Windows Defender SmartScreen</div>
                                            <div style="font-size:0.8rem; color:#333; margin-bottom:2px;"><strong>App:</strong> Sparrow-2.1.0.exe</div>
                                            <div style="font-size:0.8rem; color:#333; margin-bottom:16px;"><strong>Publisher:</strong> Unknown publisher</div>
                                            <div style="display:flex; justify-content:flex-end; gap:8px;">
                                                <button class="mock-btn mock-btn-grey">Don't run</button>
                                                <button class="mock-btn" style="background:#1e88cf; color:white; font-weight:600;">Run anyway</button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">5</div>
                        <div class="ui-step-content">
                            <p><strong>Open Sparrow from the Start menu — you are done!</strong><br>
                            Click the Windows Start button (bottom-left), type <strong>Sparrow</strong> and click it. Sparrow opens with its dark window and logo. You will see the wallet setup screen — that means everything worked perfectly.</p>
                            <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:flex-start; margin:10px 0;">
                                <div class="ui-mockup" style="max-width:220px;">
                                    <div class="mock-titlebar-win">
                                        <span>⊞  Search</span>
                                    </div>
                                    <div class="mock-body" style="background:#1a1a2e; padding:10px 12px;">
                                        <div style="background:rgba(255,255,255,0.08); border-radius:4px; padding:5px 8px; font-size:0.78rem; color:#aaa; margin-bottom:8px;">🔍 sparrow</div>
                                        <div style="background:rgba(255,255,255,0.15); border-radius:6px; padding:8px 10px; display:flex; align-items:center; gap:8px;">
                                            <img src="/static/sparrow-logo.png" alt="" style="width:22px; height:22px;">
                                            <div>
                                                <div style="color:white; font-size:0.82rem; font-weight:600;">Sparrow Wallet</div>
                                                <div style="color:rgba(255,255,255,0.45); font-size:0.7rem;">App</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div style="font-size:1.6rem; color:#bbb; flex:none; align-self:center;">→</div>
                                <div class="ui-mockup" style="max-width:280px;">
                                    <div style="background:#131415; padding:5px 10px; display:flex; align-items:center; justify-content:space-between;">
                                        <div style="display:flex; align-items:center; gap:6px;">
                                            <img src="/static/sparrow-logo.png" alt="" style="width:16px; height:16px;">
                                            <span style="color:#ccc; font-size:0.8rem;">Sparrow Wallet</span>
                                        </div>
                                        <div><span class="ui-win-btn">─</span><span class="ui-win-btn">□</span><span class="ui-win-btn" style="background:#c42b1c;color:white;border-color:#c42b1c;">✕</span></div>
                                    </div>
                                    <div style="background:#1b1d1f; padding:0; border-bottom:1px solid #333;">
                                        <div style="display:flex; gap:0; font-size:0.78rem;">
                                            <span style="padding:5px 10px; color:#ccc; cursor:default;">File</span>
                                            <span style="padding:5px 10px; color:#ccc; cursor:default;">View</span>
                                            <span style="padding:5px 10px; color:#ccc; cursor:default;">Tool</span>
                                            <span style="padding:5px 10px; color:#ccc; cursor:default;">Help</span>
                                        </div>
                                    </div>
                                    <div style="background:#1b1d1f; padding:20px 16px; text-align:center;">
                                        <img src="/static/sparrow-logo.png" alt="Sparrow" style="width:42px; height:42px; margin-bottom:8px;">
                                        <div style="color:white; font-weight:600; font-size:0.9rem; margin-bottom:4px;">Welcome to Sparrow</div>
                                        <div style="color:#888; font-size:0.72rem; margin-bottom:14px;">A Bitcoin Wallet for those who value financial self sovereignty</div>
                                        <button class="mock-btn" style="background:#1e88cf; color:white; width:100%; padding:6px 0; font-size:0.82rem; margin-bottom:6px;">Create New Wallet</button>
                                        <button class="mock-btn mock-btn-grey" style="width:100%; padding:6px 0; font-size:0.82rem;">Import Wallet</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

            <!-- ===== MACOS ===== -->
            <div data-os="macos" style="margin-top:12px;">
                <div class="ui-guide">

                    <div class="ui-step">
                        <div class="ui-step-label">1</div>
                        <div class="ui-step-content">
                            <p><strong>Open your browser and go to the Sparrow download page.</strong><br>
                            Type <strong>sparrowwallet.com/download</strong> in the address bar and press Enter. Find the macOS section and click the <code>.dmg</code> file for your chip (Apple M-series or Intel).</p>
                            <img class="zoomable" src="/static/sparrow-download-page.png" alt="Sparrow download page" style="width:100%; max-width:520px; border-radius:8px; border:1px solid #ddd; display:block; margin:10px 0;">
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">2</div>
                        <div class="ui-step-content">
                            <p><strong>Find the downloaded file and open it.</strong><br>
                            Safari shows a downloads icon (↓) at the top right of the browser. Click it, then click the file name to open it — OR open <strong>Finder</strong>, click <strong>Downloads</strong> in the left sidebar, and double-click the <code>.dmg</code> file.</p>
                            <div style="display:flex; gap:12px; flex-wrap:wrap; margin:10px 0; align-items:flex-start;">
                                <div>
                                    <div style="font-size:0.78rem; color:#888; margin-bottom:5px;">Safari downloads list:</div>
                                    <div class="ui-mockup" style="max-width:240px;">
                                        <div class="mock-titlebar-mac">
                                            <span class="mock-dot" style="background:#ff5f57;"></span>
                                            <span class="mock-dot" style="background:#febc2e;"></span>
                                            <span class="mock-dot" style="background:#28c840;"></span>
                                            <span style="flex:1; text-align:right; font-size:0.75rem;">Downloads</span>
                                        </div>
                                        <div class="mock-body" style="padding:10px 12px;">
                                            <div style="display:flex; align-items:center; gap:8px; padding:6px 8px; border-radius:6px; background:#f0f0f0;">
                                                <img src="/static/sparrow-logo.png" alt="" style="width:28px; height:28px;">
                                                <div>
                                                    <div style="font-size:0.82rem; font-weight:600;">Sparrow-2.1.0.dmg</div>
                                                    <div style="font-size:0.72rem; color:#888;">Done — 120 MB</div>
                                                </div>
                                                <span style="color:#007AFF; font-size:0.75rem; margin-left:auto; cursor:default;">🔍</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div>
                                    <div style="font-size:0.78rem; color:#888; margin-bottom:5px;">Or Finder → Downloads:</div>
                                    <div class="ui-mockup" style="max-width:250px;">
                                        <div class="mock-titlebar-mac">
                                            <span class="mock-dot" style="background:#ff5f57;"></span>
                                            <span class="mock-dot" style="background:#febc2e;"></span>
                                            <span class="mock-dot" style="background:#28c840;"></span>
                                            <span style="flex:1; text-align:center; font-size:0.78rem;">Downloads</span>
                                        </div>
                                        <div class="mock-body" style="display:flex; padding:10px 12px; gap:6px; flex-wrap:wrap;">
                                            <div style="text-align:center; opacity:0.35; width:56px;"><div style="font-size:2rem;">📄</div><div style="font-size:0.66rem;">file.pdf</div></div>
                                            <div style="text-align:center; outline:2px solid #007AFF; border-radius:6px; padding:3px; width:56px;">
                                                <img src="/static/sparrow-logo.png" alt="" style="width:32px; height:32px; display:block; margin:0 auto;">
                                                <div style="font-size:0.66rem; margin-top:2px;">Sparrow-2.1.0.dmg</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">3</div>
                        <div class="ui-step-content">
                            <p><strong>Drag Sparrow into Applications.</strong><br>
                            When you open the .dmg a window appears with the Sparrow icon and a shortcut to your Applications folder. <strong>Click and hold</strong> the Sparrow icon, <strong>drag it</strong> across the arrow, and <strong>drop it</strong> on the Applications folder. Then close and eject the .dmg window.</p>
                            <div class="ui-mockup" style="max-width:360px;">
                                <div class="mock-titlebar-mac">
                                    <span class="mock-dot" style="background:#ff5f57;"></span>
                                    <span class="mock-dot" style="background:#febc2e;"></span>
                                    <span class="mock-dot" style="background:#28c840;"></span>
                                    <span style="flex:1; text-align:center; font-size:0.78rem;">Sparrow 2.1.0</span>
                                </div>
                                <div class="mock-body" style="display:flex; align-items:center; justify-content:space-around; padding:28px 20px;">
                                    <div style="text-align:center;">
                                        <img src="/static/sparrow-logo.png" alt="Sparrow" style="width:52px; height:52px;">
                                        <div style="font-size:0.78rem; color:#444; margin-top:6px;">Sparrow</div>
                                    </div>
                                    <div style="font-size:2.2rem; color:#bbb;">→</div>
                                    <div style="text-align:center;">
                                        <div style="font-size:3.2rem;">📁</div>
                                        <div style="font-size:0.78rem; color:#444; margin-top:2px;">Applications</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">4</div>
                        <div class="ui-step-content">
                            <p><strong>Gatekeeper warning — do not panic!</strong><br>
                            The first time you open Sparrow, macOS will block it saying "unidentified developer." <strong>Do not try to double-click it normally.</strong> Instead: open <strong>Finder → Applications</strong>, find Sparrow, then <strong>right-click</strong> (or hold Ctrl and click) the icon and choose <strong>Open</strong>. A different dialog appears with an Open button — click it once to allow Sparrow forever.</p>
                            <div style="display:flex; gap:10px; flex-wrap:wrap; margin:10px 0; align-items:flex-start;">
                                <div>
                                    <div style="font-size:0.75rem; color:#888; margin-bottom:5px;">① Double-click → blocked:</div>
                                    <div class="ui-mockup" style="max-width:240px;">
                                        <div class="mock-titlebar-mac">
                                            <span class="mock-dot" style="background:#ff5f57;"></span>
                                            <span class="mock-dot" style="background:#febc2e;"></span>
                                            <span class="mock-dot" style="background:#28c840;"></span>
                                        </div>
                                        <div class="mock-body" style="text-align:center; padding:18px 14px;">
                                            <img src="/static/sparrow-logo.png" alt="" style="width:40px; height:40px; margin-bottom:8px;">
                                            <div style="font-size:0.85rem; font-weight:600; margin-bottom:6px;">"Sparrow" can't be opened</div>
                                            <p style="font-size:0.78rem; color:#555; margin:0 0 14px;">Apple cannot verify it is free from malware.</p>
                                            <button class="mock-btn mock-btn-mac" style="width:100%; padding:6px 0; font-size:0.82rem;">OK</button>
                                        </div>
                                    </div>
                                </div>
                                <div style="align-self:center; font-size:1.4rem; color:#bbb; text-align:center; line-height:1.5; flex:none; margin-top:20px;">→<br><span style="font-size:0.7rem; color:#888;">right-click<br>→ Open</span></div>
                                <div>
                                    <div style="font-size:0.75rem; color:#888; margin-bottom:5px;">② Right-click → Open → click Open:</div>
                                    <div class="ui-mockup" style="max-width:240px;">
                                        <div class="mock-titlebar-mac">
                                            <span class="mock-dot" style="background:#ff5f57;"></span>
                                            <span class="mock-dot" style="background:#febc2e;"></span>
                                            <span class="mock-dot" style="background:#28c840;"></span>
                                        </div>
                                        <div class="mock-body" style="text-align:center; padding:18px 14px;">
                                            <img src="/static/sparrow-logo.png" alt="" style="width:40px; height:40px; margin-bottom:8px;">
                                            <div style="font-size:0.85rem; font-weight:600; margin-bottom:6px;">"Sparrow" is from an unidentified developer</div>
                                            <p style="font-size:0.78rem; color:#555; margin:0 0 14px;">Are you sure you want to open it?</p>
                                            <div style="display:flex; gap:8px;">
                                                <button class="mock-btn mock-btn-grey" style="flex:1; padding:6px 0; font-size:0.78rem;">Cancel</button>
                                                <button class="mock-btn mock-btn-mac" style="flex:1; padding:6px 0; font-size:0.78rem; font-weight:600;">Open</button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">5</div>
                        <div class="ui-step-content">
                            <p><strong>Sparrow opens — you are done!</strong><br>
                            Sparrow launches with its dark window. You will see the menu bar at the top and the wallet setup screen. That means it is installed correctly.</p>
                            <div class="ui-mockup" style="max-width:300px;">
                                <div class="mock-titlebar-mac" style="background:#131415; border-bottom:1px solid #2a2a2a;">
                                    <span class="mock-dot" style="background:#ff5f57;"></span>
                                    <span class="mock-dot" style="background:#febc2e;"></span>
                                    <span class="mock-dot" style="background:#28c840;"></span>
                                    <span style="flex:1; text-align:center; color:#888; font-size:0.78rem;">Sparrow Wallet</span>
                                </div>
                                <div style="background:#1b1d1f; padding:0; border-bottom:1px solid #2a2a2a;">
                                    <div style="display:flex; font-size:0.78rem; padding:2px 4px;">
                                        <span style="padding:4px 8px; color:#ccc;">File</span>
                                        <span style="padding:4px 8px; color:#ccc;">View</span>
                                        <span style="padding:4px 8px; color:#ccc;">Tool</span>
                                        <span style="padding:4px 8px; color:#ccc;">Help</span>
                                    </div>
                                </div>
                                <div style="background:#1b1d1f; padding:20px 16px; text-align:center;">
                                    <img src="/static/sparrow-logo.png" alt="Sparrow" style="width:44px; height:44px; margin-bottom:8px;">
                                    <div style="color:white; font-weight:600; font-size:0.9rem; margin-bottom:4px;">Welcome to Sparrow</div>
                                    <div style="color:#888; font-size:0.72rem; margin-bottom:14px;">A Bitcoin Wallet for those who value financial self sovereignty</div>
                                    <button class="mock-btn" style="background:#1e88cf; color:white; width:100%; padding:6px 0; font-size:0.82rem; margin-bottom:6px;">Create New Wallet</button>
                                    <button class="mock-btn mock-btn-grey" style="width:100%; padding:6px 0; font-size:0.82rem;">Import Wallet</button>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

            <!-- ===== LINUX ===== -->
            <div data-os="linux" style="margin-top:12px;">
                <div class="ui-guide">

                    <div class="ui-step">
                        <div class="ui-step-label">1</div>
                        <div class="ui-step-content">
                            <p><strong>Open your browser and go to the Sparrow download page.</strong><br>
                            Type <strong>sparrowwallet.com/download</strong> in your browser address bar and press Enter. For Ubuntu/Debian, click the <strong>Linux (Intel/AMD) (Ubuntu/Debian)</strong> link to download the <code>.deb</code> package.</p>
                            <img class="zoomable" src="/static/sparrow-download-page.png" alt="Sparrow download page" style="width:100%; max-width:520px; border-radius:8px; border:1px solid #ddd; display:block; margin:10px 0;">
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">2</div>
                        <div class="ui-step-content">
                            <p><strong>Open a Terminal.</strong><br>
                            A Terminal is a text window where you type commands. You need it to install the file. Here are three ways to open one — use whichever works on your system:</p>
                            <div style="display:flex; gap:10px; flex-wrap:wrap; margin:10px 0;">
                                <div style="background:#f0f7ff; border:1px solid #c5ddf4; border-radius:8px; padding:12px 14px; flex:1; min-width:140px; font-size:0.85rem;">
                                    <div style="font-weight:600; margin-bottom:4px;">⌨ Keyboard shortcut</div>
                                    <div>Press <code>Ctrl + Alt + T</code> at the same time. Works on most Linux desktops (Ubuntu, Mint, etc.).</div>
                                </div>
                                <div style="background:#f0f7ff; border:1px solid #c5ddf4; border-radius:8px; padding:12px 14px; flex:1; min-width:140px; font-size:0.85rem;">
                                    <div style="font-weight:600; margin-bottom:4px;">🔍 App search</div>
                                    <div>Press the Super key (Windows key) or open your app menu, type <strong>Terminal</strong> and click it.</div>
                                </div>
                                <div style="background:#f0f7ff; border:1px solid #c5ddf4; border-radius:8px; padding:12px 14px; flex:1; min-width:140px; font-size:0.85rem;">
                                    <div style="font-weight:600; margin-bottom:4px;">🖱 Right-click desktop</div>
                                    <div>Right-click on an empty area of your desktop. If you see <strong>Open Terminal Here</strong>, click it.</div>
                                </div>
                            </div>
                            <div class="ui-mockup" style="max-width:380px; margin-top:4px;">
                                <div class="mock-titlebar-linux">
                                    <span class="mock-dot" style="background:#ff5f57;"></span>
                                    <span class="mock-dot" style="background:#febc2e;"></span>
                                    <span class="mock-dot" style="background:#28c840;"></span>
                                    <span style="margin-left:8px;">Terminal</span>
                                </div>
                                <div class="mock-terminal">
                                    <div><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~$</span> <span style="animation:blink 1s step-end infinite;">█</span></div>
                                    <div style="color:#666; font-size:0.75rem; margin-top:4px;">← a blinking cursor means the terminal is ready</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">3</div>
                        <div class="ui-step-content">
                            <p><strong>Go to your Downloads folder.</strong><br>
                            In the terminal, type the command below and press Enter. This moves you into the Downloads folder where the Sparrow file was saved. The <code>ls</code> command lists the files so you can confirm the Sparrow file is there.</p>
                            <div class="ui-mockup" style="max-width:440px;">
                                <div class="mock-titlebar-linux">
                                    <span class="mock-dot" style="background:#ff5f57;"></span>
                                    <span class="mock-dot" style="background:#febc2e;"></span>
                                    <span class="mock-dot" style="background:#28c840;"></span>
                                    <span style="margin-left:8px;">Terminal</span>
                                </div>
                                <div class="mock-terminal">
                                    <div><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~$</span> cd ~/Downloads</div>
                                    <div style="color:#666; font-size:0.75rem; padding-left:8px; margin:1px 0;">← this moves you into the Downloads folder</div>
                                    <div style="margin-top:4px;"><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~/Downloads$</span> ls</div>
                                    <div style="color:#d4d4d4; margin-top:2px; padding-left:8px;">sparrow-2.1.0-1.x86_64.deb</div>
                                    <div style="color:#666; font-size:0.75rem; padding-left:8px; margin:1px 0;">← you can see the Sparrow file is here</div>
                                    <div style="margin-top:4px;"><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~/Downloads$</span> <span>█</span></div>
                                </div>
                            </div>
                            <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin-top:8px; position:relative;">
                                <pre id="linux-cd-downloads" style="margin:0; font-size:0.85rem;">cd ~/Downloads</pre>
                                <button onclick="copyToClipboard('linux-cd-downloads')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">4</div>
                        <div class="ui-step-content">
                            <p><strong>Install Sparrow.</strong><br>
                            Run the command below. It installs the <code>.deb</code> package. It will ask for your <strong>password</strong> — type it and press Enter. Nothing shows on screen while you type — that is normal, it is just a security feature.</p>
                            <div class="ui-mockup" style="max-width:460px;">
                                <div class="mock-titlebar-linux">
                                    <span class="mock-dot" style="background:#ff5f57;"></span>
                                    <span class="mock-dot" style="background:#febc2e;"></span>
                                    <span class="mock-dot" style="background:#28c840;"></span>
                                    <span style="margin-left:8px;">Terminal — ~/Downloads</span>
                                </div>
                                <div class="mock-terminal">
                                    <div><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~/Downloads$</span> sudo dpkg -i sparrow-*.deb</div>
                                    <div style="color:#666; font-size:0.75rem; padding-left:8px; margin:2px 0;">[sudo] password for user: <span style="color:#aaa;">········</span>  ← type your password here, then press Enter</div>
                                    <div style="color:#d4d4d4; margin-top:4px;">Selecting previously unselected package sparrow.</div>
                                    <div style="color:#d4d4d4;">Unpacking sparrow (2.1.0) ...</div>
                                    <div style="color:#d4d4d4;">Setting up sparrow (2.1.0) ...</div>
                                    <div style="color:#28c840; margin-top:2px;">Done.</div>
                                    <div style="margin-top:4px;"><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~/Downloads$</span> <span>█</span></div>
                                </div>
                            </div>
                            <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin-top:8px; position:relative;">
                                <pre id="sparrow-deb" style="margin:0; font-size:0.85rem;">sudo dpkg -i sparrow-*.deb</pre>
                                <button onclick="copyToClipboard('sparrow-deb')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">5</div>
                        <div class="ui-step-content">
                            <p><strong>Launch Sparrow — you are done!</strong><br>
                            Open your applications menu, search for <strong>Sparrow Wallet</strong> and click it. You will see Sparrow's dark window with the wallet setup screen — that means it is installed correctly.</p>
                            <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:flex-start; margin:10px 0;">
                                <div>
                                    <div style="font-size:0.78rem; color:#888; margin-bottom:5px;">App launcher — search "Sparrow":</div>
                                    <img class="zoomable" src="/static/sparrow-app-launcher.png" alt="App launcher showing Sparrow" style="width:100%; max-width:340px; border-radius:8px; border:1px solid #ddd; display:block;">
                                </div>
                                <div style="font-size:1.6rem; color:#bbb; align-self:center; flex:none;">→</div>
                                <div>
                                    <div style="font-size:0.78rem; color:#888; margin-bottom:5px;">Sparrow opens — installation complete:</div>
                                    <img class="zoomable" src="/static/sparrow-welcome.png" alt="Sparrow welcome screen" style="width:100%; max-width:340px; border-radius:8px; border:1px solid #ddd; display:block;">
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

            <p style="margin-top:20px; color:#777; font-size:0.9rem;">Want a video walkthrough? The Plan B Academy tutorial shows the full installation with real screenshots. <strong>Stop when you reach "Server Configuration" — then come back here for Step 2.</strong></p>
            <div style="margin:10px 0 4px;">
                <a href="https://planb.academy/en/tutorials/wallet/desktop/sparrow-c674e2ac-d46f-4c82-92a7-7d1b0e262f5d" target="_blank"
                   style="display:inline-flex; align-items:center; gap:10px; background: linear-gradient(135deg, #f7931a, #ff9800); color:white; padding:14px 24px; border-radius:10px; font-weight:bold; font-size:1rem; text-decoration:none;">
                    &#127891; Open the Sparrow Wallet Tutorial on Plan B Academy
                </a>
            </div>

            <p style="margin-top:24px;"><strong>Step 2 — Switch Sparrow to Signet (practice mode)</strong></p>
            <p>By default Sparrow is set up for real Bitcoin. We need to switch it to Signet — the practice network — so everything you do here stays safe and costs nothing.</p>
            <p>In Sparrow, click <strong>Tools</strong> in the top menu, then hover over <strong>Restart in</strong>, and click <strong>Signet</strong>. <em>Don't worry — this is normal. Sparrow is just restarting in Signet mode, nothing is lost.</em></p>
            <div class="ui-guide">
                <div class="ui-step">
                    <div class="ui-step-label">A</div>
                    <div class="ui-step-content">
                        <p>Click <strong>Tools</strong> in the top menu, hover over <strong>Restart in</strong>, then click <strong>Signet</strong>.</p>
                        <img class="zoomable" src="/static/sparrow-restart-signet.png" alt="Sparrow Tools menu showing Restart in Signet" style="max-width:480px; border-radius:8px; box-shadow:0 4px 14px rgba(0,0,0,0.18); border:1px solid #ccc;">
                    </div>
                </div>
            </div>
            <div style="background:#f0fff4; border-left:4px solid #4caf50; border-radius:8px; padding:12px 16px; margin:10px 0;">
                <strong>✓ How to confirm it worked:</strong> After Sparrow restarts, look at the title bar at the very top of the window. It should now say <strong>Sparrow [Signet]</strong> instead of just <strong>Sparrow</strong>. If you see that, you are in practice mode and ready to continue.
            </div>

            <p style="margin-top:24px;"><strong>Step 3 — Connect Sparrow to a Bitcoin node</strong></p>
            <p>Now we need to point Sparrow at a node so it can see the Signet network.</p>
            <div class="ui-guide">
                <div class="ui-step">
                    <div class="ui-step-label">A</div>
                    <div class="ui-step-content">
                        <p>Click <strong>File</strong> in the top menu bar, then click <strong>Settings...</strong> near the bottom of the list.</p>
                        <img class="zoomable" src="/static/sparrow-file-menu.png" alt="Sparrow File menu showing Settings option" style="max-width:300px; border-radius:8px; box-shadow:0 4px 14px rgba(0,0,0,0.18); border:1px solid #ccc;">
                    </div>
                </div>
                <div class="ui-step">
                    <div class="ui-step-label">B</div>
                    <div class="ui-step-content">
                        <p>A settings window opens. Click <strong>Server</strong> in the left sidebar. Then under <strong>Type</strong>, click <strong>Bitcoin Core</strong>.</p>
                        <p style="color:#777; font-size:0.9rem;">You will see three options: Public Server, Bitcoin Core, and Private Electrum. <strong>Bitcoin Core is the best available option for this task.</strong></p>
                        <img class="zoomable" src="/static/sparrow-server-settings.png" alt="Sparrow Server settings with Bitcoin Core selected" style="max-width:480px; border-radius:8px; box-shadow:0 4px 14px rgba(0,0,0,0.18); border:1px solid #ccc;">
                    </div>
                </div>
                <div class="ui-step">
                    <div class="ui-step-label">C</div>
                    <div class="ui-step-content">
                        <p>In the <strong>URL</strong> field, click the small arrow (▾) to open the dropdown and pick a Signet node from the list.</p>
                        <p style="color:#777; font-size:0.9rem;">If the list is empty or no Signet nodes appear, enter the details below manually. Set Authentication to <strong>User / Pass</strong> and fill in the fields as shown:</p>
                        <div style="background:#f8f9fa; border:1px solid #ddd; border-radius:8px; padding:14px 18px; margin:10px 0; font-size:0.88rem;">
                            <div style="display:grid; grid-template-columns:120px 1fr; gap:6px 12px; align-items:center;">
                                <span style="color:#777;">URL</span><code>86.104.228.47</code>
                                <span style="color:#777;">Port</span><code>38332</code>
                                <span style="color:#777;">Authentication</span><span>User / Pass</span>
                                <span style="color:#777;">Username</span><code>devuser</code>
                                <span style="color:#777;">Password</span><code>devpass123</code>
                            </div>
                        </div>
                        <p style="color:#777; font-size:0.9rem;">If you have your own Bitcoin node running, you can enter its details here instead.</p>
                    </div>
                </div>
                <div class="ui-step">
                    <div class="ui-step-label">D</div>
                    <div class="ui-step-content">
                        <p>Click the <strong>Test Connection</strong> button. If everything is correct you will see a green success message. If not, try selecting a different node from the dropdown and test again.</p>
                    </div>
                </div>
            </div>

            <p style="margin-top:24px;"><strong>Step 4 — Create your wallet</strong></p>
            <p>Go back to the Plan B Academy tutorial and continue from the <strong>"Creating a New Wallet"</strong> section. Since Sparrow is now in Signet mode, any wallet you create will be a practice wallet — perfect for testing.</p>

            <p style="margin-top:20px;"><strong>Step 5 — Get your receive address</strong></p>
            <p>In Sparrow, click the <strong>Receive</strong> tab — it is in the left sidebar of your open wallet. You will see a long string of letters and numbers — this is your Signet address. It will start with <code>tb1</code>, for example: <code>tb1q3x7k2m...</code>.</p>
            <p>You need to copy this address so you can paste it into the faucet. Here is how to do it:</p>

            <div data-os="windows" style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:14px 18px; margin:10px 0;">
                <strong>🪟 Copying and pasting on Windows</strong>
                <ol style="margin:8px 0 0; padding-left:20px; background:none; border:none;">
                    <li>In Sparrow, click on the address to select it, or right-click it and choose <strong>Copy</strong></li>
                    <li>Alternatively, click the address once, then press <code>Ctrl + A</code> to select all, then <code>Ctrl + C</code> to copy</li>
                    <li>Go to the faucet page, click inside the address box, and press <code>Ctrl + V</code> to paste</li>
                </ol>
                <p style="margin:8px 0 0; font-size:0.88rem; color:#777;">Sparrow also has a small copy icon next to the address — clicking it copies it automatically.</p>
            </div>
            <div data-os="macos" style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:14px 18px; margin:10px 0;">
                <strong>🍎 Copying and pasting on macOS</strong>
                <ol style="margin:8px 0 0; padding-left:20px; background:none; border:none;">
                    <li>In Sparrow, click on the address to select it, or right-click it and choose <strong>Copy</strong></li>
                    <li>Alternatively, click the address once, then press <code>Cmd + A</code> to select all, then <code>Cmd + C</code> to copy</li>
                    <li>Go to the faucet page, click inside the address box, and press <code>Cmd + V</code> to paste</li>
                </ol>
                <p style="margin:8px 0 0; font-size:0.88rem; color:#777;">Sparrow also has a small copy icon next to the address — clicking it copies it automatically.</p>
            </div>
            <div data-os="linux" style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:14px 18px; margin:10px 0;">
                <strong>🐧 Copying and pasting on Linux</strong>
                <ol style="margin:8px 0 0; padding-left:20px; background:none; border:none;">
                    <li>In Sparrow, click on the address to select it, or right-click it and choose <strong>Copy</strong></li>
                    <li>Alternatively, click the address once, then press <code>Ctrl + A</code> to select all, then <code>Ctrl + C</code> to copy</li>
                    <li>Go to the faucet page, click inside the address box, and press <code>Ctrl + V</code> to paste</li>
                </ol>
                <p style="margin:8px 0 0; font-size:0.88rem; color:#777;">On Linux you can also highlight text with your mouse and middle-click anywhere in the address box to paste it directly.</p>
            </div>

            <p style="margin-top:20px;"><strong>Step 6 — Get your test coins</strong></p>
            <p><a href="#" onclick="showSection('faucetSection'); return false;">Go to the faucet →</a>, paste your address into the box, complete the code check, and click <strong>Get Coins</strong>.</p>

            <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:14px 18px; margin:16px 0;">
                <strong>What happens next?</strong>
                <p style="margin:8px 0 0;">Your coins won't appear instantly. The network needs to confirm your transaction first, which happens when the next block is mined — this usually takes a few minutes. You can track your transaction using the Transaction ID shown after you submit, and the Signet Explorer link on the faucet page.</p>
            </div>

            <div style="margin-top:20px;">
                <strong>&#128736; Troubleshooting — click a problem to see the fix</strong>
                <div style="margin-top:10px;">
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            Test Connection failed <span>＋</span>
                        </button>
                        <div class="faq-answer">Make sure you selected or entered a Signet node, not a mainnet one. If entering manually, double-check the URL, port, and credentials. Also confirm Sparrow is running in Signet mode — the title bar should say <strong>Sparrow [Signet]</strong>.</div>
                    </div>
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            Invalid address error on the faucet <span>＋</span>
                        </button>
                        <div class="faq-answer">Make sure you copied the full address from the Receive tab and that it starts with <code>tb1</code>. Do not use a mainnet Bitcoin address here — those start with <code>bc1</code> and will be rejected.</div>
                    </div>
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            Coins not showing up in Sparrow <span>＋</span>
                        </button>
                        <div class="faq-answer">Wait a few minutes — coins only appear after the next block is mined on the network. If nothing arrives after 10 minutes, copy the Transaction ID shown on the faucet page and paste it into the Signet Explorer to check its status.</div>
                    </div>
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            Sparrow shows "Disconnected" at the bottom <span>＋</span>
                        </button>
                        <div class="faq-answer">Your node connection dropped. Go to <strong>File → Settings → Server</strong>, click <strong>Test Connection</strong> again. If it still fails, try selecting a different node from the dropdown.</div>
                    </div>
                </div>
            </div>

            <div style="margin-top: 28px; background: #fff3cd; border-left: 5px solid #f7931a; border-radius: 10px; padding: 16px 20px;">
                <strong>⚠️ Important — if you ever use Sparrow with real Bitcoin:</strong>
                <p style="margin: 10px 0 0;">For Signet practice, connecting to a third-party node is completely fine — the coins have no value. But if you switch to real Bitcoin, that node can see your wallet addresses and transaction history. For real funds, you should run your own Bitcoin node (Core, Knots, or another implementation) and ideally your own Electrum server alongside it. This keeps your finances private and means you are not trusting anyone else's infrastructure. The Plan B Academy has courses to help you get there when you are ready.</p>
            </div>
        </div>

        <div class="section-wrapper hidden" id="guideSection">
            <button class="back-button" onclick="showSection('newUserSection')">← Back</button>
            <h2>Advanced Setup — Run Your Own Bitcoin Node</h2>
            <p>Running your own Bitcoin node means your computer independently verifies every transaction and block on the network — you don't have to trust anyone else. It's the most private and self-sovereign way to use Bitcoin. This guide walks you through setting one up on Signet (the practice network) so you can learn safely.</p>

            <div style="background:#fff8e1; border-left:4px solid #f7931a; border-radius:8px; padding:14px 18px; margin:16px 0;">
                <strong>Before you start — what do you need?</strong>
                <ul style="margin:8px 0 0; padding-left:20px; background:none; border:none;">
                    <li>A computer running Windows, macOS, or Linux</li>
                    <li>About 30–60 minutes and a stable internet connection</li>
                    <li>Comfort using a terminal (command line) — we'll guide you through every step</li>
                </ul>
            </div>

            <div style="margin:24px 0 10px;">
                <p style="margin-bottom:10px; font-weight:bold;">&#128187; Select your operating system — the guide will update automatically:</p>
                <div style="display:flex; gap:10px; flex-wrap:wrap;">
                    <button class="os-tab-btn" data-os="windows" onclick="selectOS('windows')">🪟 Windows</button>
                    <button class="os-tab-btn" data-os="macos" onclick="selectOS('macos')">🍎 macOS</button>
                    <button class="os-tab-btn" data-os="linux" onclick="selectOS('linux')">🐧 Linux</button>
                </div>
            </div>

            <p style="margin-top:20px;"><strong>Step 1 — Choose a Bitcoin node to install</strong></p>
            <p>There are several Bitcoin node implementations to choose from. They all follow the same rules and work on the same network — they just differ in features and philosophy. Pick one:</p>

            <p style="margin:10px 0 6px; font-weight:600;">Click the one you want to install — the guide will update automatically:</p>
            <div style="display:flex; gap:12px; flex-wrap:wrap; margin:10px 0 6px;">
                <div id="card-core" onclick="selectNode('core')" style="flex:1; min-width:200px; background:#f8f9fa; border:2px solid #f7931a; border-radius:12px; padding:16px 18px; cursor:pointer; transition:all 0.15s;">
                    <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
                        <img src="/static/bitcoin-core-logo.png" alt="Bitcoin Core" style="width:36px; height:36px; border-radius:6px;">
                        <strong style="font-size:1rem;">Bitcoin Core</strong>
                        <span id="check-core" style="margin-left:auto; color:#f7931a; font-size:1.2rem;">✓</span>
                    </div>
                    <p style="margin:0; font-size:0.85rem; color:#555;">A widely used Bitcoin node. Well tested and backed by a large developer community.</p>
                    <button class="hint-btn" style="color:#f7931a; border-color:#f7931a; margin-top:8px;" onclick="event.stopPropagation(); toggleInfo('info-core')">i</button>
                    <div id="info-core" class="hint-box">A Bitcoin node implementation maintained since 2009. Widely used and well tested, supported by a large developer community. A reliable choice for running your own node.</div>
                </div>
                <div id="card-knots" onclick="selectNode('knots')" style="flex:1; min-width:200px; background:#f8f9fa; border:2px solid #e0e0e0; border-radius:12px; padding:16px 18px; cursor:pointer; transition:all 0.15s;">
                    <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
                        <img src="/static/bitcoin-knots-logo.png" alt="Bitcoin Knots" style="width:36px; height:36px; border-radius:6px;">
                        <strong style="font-size:1rem;">Bitcoin Knots</strong>
                        <span id="check-knots" style="margin-left:auto; color:#f7931a; font-size:1.2rem; display:none;">✓</span>
                    </div>
                    <p style="margin:0; font-size:0.85rem; color:#555;">A Bitcoin node with extra features and stricter defaults. More control over your node.</p>
                    <button class="hint-btn" style="color:#f7931a; border-color:#f7931a; margin-top:8px;" onclick="event.stopPropagation(); toggleInfo('info-knots')">i</button>
                    <div id="info-knots" class="hint-box">A Bitcoin node with additional features and stricter defaults — gives you more control over which transactions your node accepts and relays. Compatible with everything Bitcoin Core supports.</div>
                </div>
            </div>
            <div style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:12px 16px; margin-bottom:10px;">
                <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                    <strong>btcd</strong>
                    <a href="https://github.com/btcsuite/btcd" target="_blank" style="font-size:0.82rem; background:#f7931a; color:white; padding:3px 10px; border-radius:12px; text-decoration:none;">GitHub</a>
                    <button class="hint-btn" style="color:#f7931a; border-color:#f7931a;" onclick="toggleInfo('info-btcd')">i</button>
                </div>
                <div id="info-btcd" class="hint-box">A full Bitcoin node written in Go. Popular in developer environments. The steps below do not cover btcd — follow its own documentation.</div>
            </div>
            <div style="background:#f8f9fa; border:1px solid #e0e0e0; border-radius:10px; padding:12px 16px; margin-bottom:16px;">
                <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                    <strong>Libbitcoin</strong>
                    <a href="https://libbitcoin.info/" target="_blank" style="font-size:0.82rem; background:#f7931a; color:white; padding:3px 10px; border-radius:12px; text-decoration:none;">Website</a>
                    <button class="hint-btn" style="color:#f7931a; border-color:#f7931a;" onclick="toggleInfo('info-libbitcoin')">i</button>
                </div>
                <div id="info-libbitcoin" class="hint-box">An independent C++ implementation. Less common for everyday use. The steps below do not cover Libbitcoin — follow its own documentation.</div>
            </div>

            <p style="margin-top:28px;"><strong>Step 2 — Download and install</strong></p>
            <p>The steps below match your selection above. You can change it any time by clicking the cards.</p>

            <div data-os="windows" style="margin-top:12px;">
                <div class="ui-guide">

                    <div class="ui-step">
                        <div class="ui-step-label">1</div>
                        <div class="ui-step-content">
                            <div data-node="core">
                                <p><strong>Download the installer</strong> — go to <a href="https://bitcoincore.org/en/download/" target="_blank">bitcoincore.org/en/download ↗</a>. Click <strong>Windows</strong> to download the <code>.exe</code> installer.</p>
                                <img class="zoomable" src="/static/bitcoin-core-download.png" alt="Bitcoin Core download page" style="width:100%; max-width:520px; border-radius:8px; border:1px solid #ddd; display:block; margin:10px 0;">
                            </div>
                            <div data-node="knots">
                                <p><strong>Download the installer</strong> — go to <a href="https://bitcoinknots.org/" target="_blank">bitcoinknots.org ↗</a>. Click <strong>Show other download formats</strong> and select the Windows <code>.exe</code> installer.</p>
                                <img class="zoomable" src="/static/bitcoin-knots-download.png" alt="Bitcoin Knots download page" style="width:100%; max-width:520px; border-radius:8px; border:1px solid #ddd; display:block; margin:10px 0;">
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">2</div>
                        <div class="ui-step-content">
                            <div data-node="core"><p><strong>Run the installer</strong> — double-click the downloaded file. A setup wizard opens. Click <strong>Next</strong> on each screen and leave all defaults, then click <strong>Install</strong>.</p></div>
                            <div data-node="knots"><p><strong>Run the installer</strong> — double-click the downloaded file. The setup wizard looks identical to Bitcoin Core's. Click <strong>Next</strong> on each screen and leave all defaults, then click <strong>Install</strong>.</p></div>
                            <div class="ui-mockup" style="max-width:340px;">
                                <div class="mock-titlebar-win">
                                    <span data-node="core">Bitcoin Core Setup</span>
                                    <span data-node="knots">Bitcoin Knots Setup</span>
                                    <span style="font-size:0.72rem; opacity:0.8;">─ □ ✕</span>
                                </div>
                                <div class="mock-body" style="text-align:center; padding:22px 20px 16px;">
                                    <div style="font-size:2.2rem; margin-bottom:8px;">₿</div>
                                    <div style="font-size:0.95rem; font-weight:600; margin-bottom:6px;">
                                        <span data-node="core">Welcome to Bitcoin Core Setup</span>
                                        <span data-node="knots">Welcome to Bitcoin Knots Setup</span>
                                    </div>
                                    <p style="color:#555; font-size:0.82rem; margin:0 0 20px;">
                                        <span data-node="core">This will install Bitcoin Core on your computer.</span>
                                        <span data-node="knots">This will install Bitcoin Knots on your computer.</span>
                                    </p>
                                    <div style="display:flex; justify-content:flex-end; gap:8px;">
                                        <button class="mock-btn mock-btn-grey">Cancel</button>
                                        <button class="mock-btn mock-btn-blue">Next →</button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">3</div>
                        <div class="ui-step-content">
                            <p><strong>Windows SmartScreen warning</strong> — Windows may show a blue warning saying it does not recognise the app. This is normal for open-source software. Click <strong>More info</strong>, then <strong>Run anyway</strong> on the next screen.</p>
                            <div style="display:flex; gap:10px; flex-wrap:wrap; margin:10px 0; align-items:center;">
                                <div class="ui-mockup" style="max-width:270px; flex:1; min-width:190px;">
                                    <div class="mock-titlebar-win" style="background:#1a4480;">
                                        <span>Windows protected your PC</span>
                                        <span style="font-size:0.72rem; opacity:0.8;">✕</span>
                                    </div>
                                    <div class="mock-body" style="background:#e8f0fb; padding:16px;">
                                        <div style="font-size:0.85rem; font-weight:600; color:#1a4480; margin-bottom:6px;">⛨ Windows Defender SmartScreen</div>
                                        <p style="font-size:0.8rem; color:#333; margin:0 0 6px;">Windows protected your PC. The app is unrecognised.</p>
                                        <div style="color:#1565C0; font-size:0.8rem; margin:0 0 14px; cursor:default;">▶ More info</div>
                                        <div style="display:flex; justify-content:flex-end;">
                                            <button class="mock-btn mock-btn-grey">Don't run</button>
                                        </div>
                                    </div>
                                </div>
                                <div style="font-size:1.6rem; color:#bbb; flex:none; align-self:center;">→</div>
                                <div class="ui-mockup" style="max-width:270px; flex:1; min-width:190px;">
                                    <div class="mock-titlebar-win" style="background:#1a4480;">
                                        <span>Windows protected your PC</span>
                                        <span style="font-size:0.72rem; opacity:0.8;">✕</span>
                                    </div>
                                    <div class="mock-body" style="background:#e8f0fb; padding:16px;">
                                        <div style="font-size:0.85rem; font-weight:600; color:#1a4480; margin-bottom:6px;">⛨ Windows Defender SmartScreen</div>
                                        <div style="font-size:0.8rem; color:#333; margin-bottom:2px;"><strong>App:</strong> <span data-node="core">bitcoin-28.0-win64-setup.exe</span><span data-node="knots">bitcoin-28.0.knots-win64-setup.exe</span></div>
                                        <div style="font-size:0.8rem; color:#333; margin-bottom:16px;"><strong>Publisher:</strong> Unknown publisher</div>
                                        <div style="display:flex; justify-content:flex-end; gap:8px;">
                                            <button class="mock-btn mock-btn-grey">Don't run</button>
                                            <button class="mock-btn mock-btn-blue">Run anyway</button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">4</div>
                        <div class="ui-step-content">
                            <p><strong>Installation complete.</strong> <span data-node="core">Bitcoin Core</span><span data-node="knots">Bitcoin Knots</span> is now on your computer. You will find it in the Start menu. You do not need to open it now — the next steps use the command line.</p>
                            <div class="ui-mockup" style="max-width:240px;">
                                <div class="mock-titlebar-win">
                                    <span>⊞ Start</span>
                                    <span style="font-size:0.72rem; opacity:0.8;">✕</span>
                                </div>
                                <div class="mock-body" style="background:#1a1a2e; padding:10px 12px;">
                                    <div style="background:rgba(255,255,255,0.08); border-radius:6px; padding:8px 12px; display:flex; align-items:center; gap:10px;">
                                        <span style="font-size:1.4rem;">₿</span>
                                        <div>
                                            <div style="color:white; font-size:0.85rem; font-weight:600;"><span data-node="core">Bitcoin Core</span><span data-node="knots">Bitcoin Knots</span></div>
                                            <div style="color:rgba(255,255,255,0.45); font-size:0.72rem;">App · Recently added</div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

            <div data-os="macos" style="margin-top:12px;">
                <div class="ui-guide">

                    <div class="ui-step">
                        <div class="ui-step-label">1</div>
                        <div class="ui-step-content">
                            <div data-node="core">
                                <p><strong>Download the disk image</strong> — go to <a href="https://bitcoincore.org/en/download/" target="_blank">bitcoincore.org/en/download ↗</a>. Click <strong>macOS (arm64)</strong> or <strong>macOS (x86_64)</strong> to download the <code>.dmg</code>.</p>
                                <img class="zoomable" src="/static/bitcoin-core-download.png" alt="Bitcoin Core download page" style="width:100%; max-width:520px; border-radius:8px; border:1px solid #ddd; display:block; margin:10px 0;">
                            </div>
                            <div data-node="knots">
                                <p><strong>Download the disk image</strong> — go to <a href="https://bitcoinknots.org/" target="_blank">bitcoinknots.org ↗</a>. Click <strong>Show other download formats</strong> and select the macOS <code>.dmg</code> file.</p>
                                <img class="zoomable" src="/static/bitcoin-knots-download.png" alt="Bitcoin Knots download page" style="width:100%; max-width:520px; border-radius:8px; border:1px solid #ddd; display:block; margin:10px 0;">
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">2</div>
                        <div class="ui-step-content">
                            <div data-node="core"><p><strong>Open the .dmg and drag to Applications</strong> — double-click the downloaded file. A window appears with the Bitcoin Core icon and your Applications folder. Drag the icon across into Applications.</p></div>
                            <div data-node="knots"><p><strong>Open the .dmg and drag to Applications</strong> — double-click the downloaded file. A window appears with the Bitcoin Knots icon and your Applications folder. Drag the icon across into Applications.</p></div>
                            <div class="ui-mockup" style="max-width:360px;">
                                <div class="mock-titlebar-mac">
                                    <span class="mock-dot" style="background:#ff5f57;"></span>
                                    <span class="mock-dot" style="background:#febc2e;"></span>
                                    <span class="mock-dot" style="background:#28c840;"></span>
                                    <span style="flex:1; text-align:center; font-size:0.78rem;"><span data-node="core">Bitcoin Core 28.0</span><span data-node="knots">Bitcoin Knots 28.0</span></span>
                                </div>
                                <div class="mock-body" style="display:flex; align-items:center; justify-content:space-around; padding:28px 20px; gap:10px;">
                                    <div style="text-align:center;">
                                        <div style="font-size:3rem; margin-bottom:4px;">₿</div>
                                        <div style="font-size:0.78rem; color:#444;"><span data-node="core">Bitcoin Core</span><span data-node="knots">Bitcoin Knots</span></div>
                                    </div>
                                    <div style="font-size:2rem; color:#bbb;">→</div>
                                    <div style="text-align:center;">
                                        <div style="font-size:3rem; margin-bottom:4px;">📁</div>
                                        <div style="font-size:0.78rem; color:#444;">Applications</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">3</div>
                        <div class="ui-step-content">
                            <p><strong>Gatekeeper warning</strong> — the first time you open Bitcoin Core, macOS will warn you it is from an unidentified developer. <strong>Do not click Open from a normal double-click.</strong> Instead: find Bitcoin Core in your Applications folder, <strong>right-click</strong> (or Ctrl+click) the icon and choose <strong>Open</strong>. A different dialog appears with an Open button — click it once to allow the app permanently.</p>
                            <div style="display:flex; gap:10px; flex-wrap:wrap; margin:10px 0; align-items:center;">
                                <div class="ui-mockup" style="max-width:250px; flex:1; min-width:180px;">
                                    <div class="mock-titlebar-mac">
                                        <span class="mock-dot" style="background:#ff5f57;"></span>
                                        <span class="mock-dot" style="background:#febc2e;"></span>
                                        <span class="mock-dot" style="background:#28c840;"></span>
                                    </div>
                                    <div class="mock-body" style="text-align:center; padding:20px 16px;">
                                        <div style="font-size:2.2rem; margin-bottom:6px;">₿</div>
                                        <div style="font-size:0.85rem; font-weight:600; margin-bottom:6px;"><span data-node="core">"Bitcoin Core" can't be opened</span><span data-node="knots">"Bitcoin Knots" can't be opened</span></div>
                                        <p style="font-size:0.78rem; color:#555; margin:0 0 14px;">Apple cannot verify it is free from malware.</p>
                                        <button class="mock-btn mock-btn-mac" style="width:100%; padding:7px 0; font-size:0.82rem;">OK</button>
                                    </div>
                                </div>
                                <div style="font-size:1.4rem; color:#bbb; flex:none; text-align:center; align-self:center; line-height:1.4;">→<br><span style="font-size:0.7rem; color:#888;">right-click<br>→ Open</span></div>
                                <div class="ui-mockup" style="max-width:250px; flex:1; min-width:180px;">
                                    <div class="mock-titlebar-mac">
                                        <span class="mock-dot" style="background:#ff5f57;"></span>
                                        <span class="mock-dot" style="background:#febc2e;"></span>
                                        <span class="mock-dot" style="background:#28c840;"></span>
                                    </div>
                                    <div class="mock-body" style="text-align:center; padding:20px 16px;">
                                        <div style="font-size:2.2rem; margin-bottom:6px;">₿</div>
                                        <div style="font-size:0.85rem; font-weight:600; margin-bottom:6px;"><span data-node="core">"Bitcoin Core" is from an unidentified developer</span><span data-node="knots">"Bitcoin Knots" is from an unidentified developer</span></div>
                                        <p style="font-size:0.78rem; color:#555; margin:0 0 14px;">Are you sure you want to open it?</p>
                                        <div style="display:flex; gap:8px;">
                                            <button class="mock-btn mock-btn-grey" style="flex:1; padding:6px 0; font-size:0.78rem;">Cancel</button>
                                            <button class="mock-btn mock-btn-mac" style="flex:1; padding:6px 0; font-size:0.78rem;">Open</button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">4</div>
                        <div class="ui-step-content">
                            <p><strong>Installation complete.</strong> <span data-node="core">Bitcoin Core</span><span data-node="knots">Bitcoin Knots</span> is now in your Applications folder. You do not need to open it now — the next steps use Terminal.</p>
                            <div class="ui-mockup" style="max-width:320px;">
                                <div class="mock-titlebar-mac">
                                    <span class="mock-dot" style="background:#ff5f57;"></span>
                                    <span class="mock-dot" style="background:#febc2e;"></span>
                                    <span class="mock-dot" style="background:#28c840;"></span>
                                    <span style="flex:1; text-align:center; font-size:0.78rem;">Applications</span>
                                </div>
                                <div class="mock-body" style="display:flex; gap:18px; padding:16px 20px; flex-wrap:wrap; align-items:center;">
                                    <div style="text-align:center; opacity:0.35;"><div style="font-size:2.2rem;">🎵</div><div style="font-size:0.72rem; color:#444; margin-top:2px;">Music</div></div>
                                    <div style="text-align:center; opacity:0.35;"><div style="font-size:2.2rem;">📷</div><div style="font-size:0.72rem; color:#444; margin-top:2px;">Photos</div></div>
                                    <div style="text-align:center; outline:2px solid #007AFF; border-radius:8px; padding:4px 8px;"><div style="font-size:2.2rem;">₿</div><div style="font-size:0.72rem; color:#444; margin-top:2px;"><span data-node="core">Bitcoin Core</span><span data-node="knots">Bitcoin Knots</span></div></div>
                                    <div style="text-align:center; opacity:0.35;"><div style="font-size:2.2rem;">🧭</div><div style="font-size:0.72rem; color:#444; margin-top:2px;">Safari</div></div>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

            <div data-os="linux" style="margin-top:12px;">
                <div class="ui-guide">

                    <div class="ui-step">
                        <div class="ui-step-label">1</div>
                        <div class="ui-step-content">
                            <div data-node="core">
                                <p><strong>Download the archive</strong> — go to <a href="https://bitcoincore.org/en/download/" target="_blank">bitcoincore.org/en/download ↗</a>. Click <strong>Linux (tgz)</strong> to download the <code>.tar.gz</code>. Most computers use <code>x86_64</code>; ARM devices use <code>aarch64</code>.</p>
                                <img class="zoomable" src="/static/bitcoin-core-download.png" alt="Bitcoin Core download page" style="width:100%; max-width:520px; border-radius:8px; border:1px solid #ddd; display:block; margin:10px 0;">
                            </div>
                            <div data-node="knots">
                                <p><strong>Download the archive</strong> — go to <a href="https://bitcoinknots.org/" target="_blank">bitcoinknots.org ↗</a>. Click <strong>Show other download formats</strong> and select the <code>.tar.gz</code> for your CPU.</p>
                                <img class="zoomable" src="/static/bitcoin-knots-download.png" alt="Bitcoin Knots download page" style="width:100%; max-width:520px; border-radius:8px; border:1px solid #ddd; display:block; margin:10px 0;">
                            </div>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">2</div>
                        <div class="ui-step-content">
                            <p><strong>Extract and install</strong> — open a terminal in your Downloads folder and run these two commands. The first unpacks the archive; the second copies the programs to <code>/usr/local/bin/</code> so you can run them from anywhere.</p>
                            <div class="ui-mockup" style="max-width:500px;">
                                <div class="mock-titlebar-linux">
                                    <span class="mock-dot" style="background:#ff5f57;"></span>
                                    <span class="mock-dot" style="background:#febc2e;"></span>
                                    <span class="mock-dot" style="background:#28c840;"></span>
                                    <span style="margin-left:6px;">Terminal — ~/Downloads</span>
                                </div>
                                <div class="mock-terminal">
                                    <div><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~/Downloads$</span> tar -xzf bitcoin-*.tar.gz</div>
                                    <div style="color:#666; font-size:0.78rem; padding-left:8px; margin:2px 0;">— extracts the files into a new folder —</div>
                                    <div><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~/Downloads$</span> sudo install -m 0755 -o root -g root -t /usr/local/bin bitcoin-*/bin/*</div>
                                    <div style="color:#666; font-size:0.78rem; padding-left:8px; margin:2px 0;">— may ask for your password —</div>
                                    <div style="margin-top:2px;"><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~/Downloads$</span> <span>█</span></div>
                                </div>
                            </div>
                            <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin-top:8px; position:relative;">
                                <pre id="linux-install" style="margin:0; font-size:0.85rem; white-space:pre-wrap;">tar -xzf bitcoin-*.tar.gz
sudo install -m 0755 -o root -g root -t /usr/local/bin bitcoin-*/bin/*</pre>
                                <button onclick="copyToClipboard('linux-install')" style="position:absolute; top:8px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                            <p style="font-size:0.85rem; color:#777; margin-top:8px;">The <code>bitcoin-*</code> wildcard works for both Bitcoin Core and Bitcoin Knots — no need to type the full filename.</p>
                        </div>
                    </div>

                    <div class="ui-step">
                        <div class="ui-step-label">3</div>
                        <div class="ui-step-content">
                            <p><strong>Verify it worked</strong> — run the command below. If a version number is printed back, the installation is complete. Both Core and Knots use the same binary name (<code>bitcoind</code>).</p>
                            <div class="ui-mockup" style="max-width:420px;">
                                <div class="mock-titlebar-linux">
                                    <span class="mock-dot" style="background:#ff5f57;"></span>
                                    <span class="mock-dot" style="background:#febc2e;"></span>
                                    <span class="mock-dot" style="background:#28c840;"></span>
                                    <span style="margin-left:6px;">Terminal — ~</span>
                                </div>
                                <div class="mock-terminal">
                                    <div><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~$</span> bitcoind --version</div>
                                    <div style="color:#d4d4d4; margin-top:2px;"><span data-node="core">Bitcoin Core version v28.0.0</span><span data-node="knots">Bitcoin Knots version v28.0.knots</span></div>
                                    <div style="color:#888; font-size:0.78rem;"><span data-node="core">Copyright (C) 2009-2024 The Bitcoin Core developers</span><span data-node="knots">Copyright (C) 2009-2024 The Bitcoin Knots developers</span></div>
                                    <div style="margin-top:4px;"><span style="color:#28c840;">user@pc</span><span style="color:#aaa;">:~$</span> <span>█</span></div>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

            <p style="margin-top:28px;"><strong>Step 3 — Find (or create) your Bitcoin data folder</strong></p>
            <p>Your node stores its configuration and data in a specific folder. You need to put the settings file there in the next step.</p>

            <div data-os="windows" style="margin-top:8px;">
                <ol>
                    <li>Press <code>Win+R</code> to open the Run box</li>
                    <li>Type <code>%APPDATA%\Bitcoin</code> and press Enter</li>
                    <li>If a folder opens, you are in the right place. If Windows says "cannot find path", you need to create it manually: open File Explorer, navigate to <code>C:\\Users\\YourName\\AppData\\Roaming\\</code> and create a new folder named <code>Bitcoin</code></li>
                </ol>
            </div>
            <div data-os="macos" style="margin-top:8px;">
                <ol>
                    <li>Open Finder</li>
                    <li>Press <code>Cmd+Shift+G</code> to open the Go to Folder box</li>
                    <li>Type <code>~/Library/Application Support/Bitcoin</code> and press Enter</li>
                    <li>If the folder does not exist yet, navigate to <code>~/Library/Application Support/</code> and create a new folder named <code>Bitcoin</code></li>
                </ol>
            </div>
            <div data-os="linux" style="margin-top:8px;">
                <ol>
                    <li>Open your home folder in the file manager</li>
                    <li>Press <code>Ctrl+H</code> to show hidden folders (names starting with a dot)</li>
                    <li>Look for a folder named <code>.bitcoin</code></li>
                    <li>If it does not exist, create it — or run this command in a terminal:</li>
                </ol>
                <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin:8px 0; position:relative;">
                    <pre id="linux-mkdir" style="margin:0; font-size:0.85rem;">mkdir -p ~/.bitcoin</pre>
                    <button onclick="copyToClipboard('linux-mkdir')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                </div>
            </div>

            <p style="margin-top:28px;"><strong>Step 4 — Create a bitcoin.conf file</strong></p>
            <p>Inside that folder, create a file named exactly <code>bitcoin.conf</code>. Open it in a plain text editor, paste the settings below, and save.</p>

            <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:12px 16px; margin:12px 0;">
                <strong>What are rpcuser and rpcpassword?</strong>
                <p style="margin:8px 0 0; font-size:0.9rem;">These are local-only credentials — they are not connected to any online account or service. Your node uses them to make sure only programs on your own computer can talk to it. You can set them to anything you like, for example <code>rpcuser=alice</code> and <code>rpcpassword=mypassword123</code>. Just make a note of what you choose, as you may need them later.</p>
            </div>

            <p><strong>Replace <code>youruser</code> and <code>yourpass</code> with your own values before saving.</strong></p>
            <div style="background:#f8f9fa; padding:10px; border-radius:5px; margin:10px 0; position:relative;">
                <pre id="bitcoin-conf" style="margin:0; white-space:pre-wrap;">signet=1
server=1
txindex=1
rpcuser=youruser
rpcpassword=yourpass
signetchallenge=512103da0ee65a81d9d035a9bfff4810c5065d647153f3396b1fde56158cdf04bbace451ae
dnsseed=0
addnode=86.104.228.47:38333</pre>
                <button onclick="copyToClipboard('bitcoin-conf')" style="position:absolute; top:10px; right:10px; background:#f7931a; color:white; border:none; padding:5px 10px; border-radius:3px; cursor:pointer;">Copy</button>
            </div>

            <div data-os="windows">
                <div class="os-warning">&#9888; <strong>Windows tip — beware of the hidden .txt extension:</strong> Windows hides file extensions by default, so Notepad often secretly saves <code>bitcoin.conf</code> as <code>bitcoin.conf.txt</code> — and the node will not find it. To prevent this: in Notepad use <em>File → Save As</em>, change "Save as type" to <strong>All Files (*.*)</strong>, and type the filename as <code>bitcoin.conf</code>. To check afterwards, enable extensions in File Explorer via <em>View → Show → File name extensions</em>.</div>
            </div>
            <div data-os="macos">
                <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:12px 16px; margin:10px 0; font-size:0.9rem;">
                    &#128187; <strong>macOS tip:</strong> Use TextEdit but switch to plain text mode first: <em>Format → Make Plain Text</em>. Rich text (.rtf) will not work. Alternatively, use a code editor like VS Code or BBEdit which handle plain text by default.
                </div>
            </div>
            <div data-os="linux">
                <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:12px 16px; margin:10px 0; font-size:0.9rem;">
                    &#128039; <strong>Linux tip:</strong> You can create and edit the file directly in the terminal with nano:
                    <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin:8px 0 0; position:relative;">
                        <pre id="linux-conf-edit" style="margin:0; font-size:0.85rem;">nano ~/.bitcoin/bitcoin.conf</pre>
                        <button onclick="copyToClipboard('linux-conf-edit')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                    </div>
                    Paste the config above, then press <code>Ctrl+O</code> to save and <code>Ctrl+X</code> to exit.
                </div>
            </div>

            <p style="margin-top:28px;"><strong>Step 5 — Open a terminal</strong></p>

            <div data-os="windows" style="margin-top:8px;">
                <p>Press <code>Win+R</code>, type <code>cmd</code>, press Enter. A black window with a blinking cursor will appear — this is the Command Prompt.</p>
                <p style="color:#777; font-size:0.88rem;">You will need to navigate to the folder where Bitcoin was installed. By default this is <code>C:\Program Files\Bitcoin\daemon\</code>. In the Command Prompt, type:</p>
                <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin:8px 0; position:relative;">
                    <pre id="win-cd" style="margin:0; font-size:0.85rem;">cd "C:\Program Files\Bitcoin\daemon"</pre>
                    <button onclick="copyToClipboard('win-cd')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                </div>
            </div>
            <div data-os="macos" style="margin-top:8px;">
                <p>Press <code>Cmd+Space</code>, type <code>Terminal</code>, press Enter. A window with a command prompt will appear.</p>
            </div>
            <div data-os="linux" style="margin-top:8px;">
                <p>Search for <strong>Terminal</strong> in your apps, or press <code>Ctrl+Alt+T</code>. Most Linux desktops have a terminal shortcut built in.</p>
            </div>

            <p style="margin-top:28px;"><strong>Step 6 — Start your node</strong></p>

            <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:12px 16px; margin:10px 0;">
                <strong>Prefer a visual interface?</strong> Bitcoin Core and Knots include a graphical app (<code>bitcoin-qt</code>) — but it <strong>cannot run at the same time as <code>bitcoind</code></strong>. They share the same data folder and one will block the other with a lock error. Choose one: either start the GUI <em>instead of</em> <code>bitcoind -daemon</code>, or stick with the terminal-only approach below.
                <ul style="margin:8px 0 0; padding-left:20px; font-size:0.88rem; background:none; border:none;">
                    <li><span data-os="windows">On Windows, find <strong>Bitcoin Core</strong> or <strong>Bitcoin Knots</strong> in the Start menu.</span><span data-os="macos">On macOS, open it from your Applications folder.</span><span data-os="linux">On Linux: first stop any running daemon with <code>bitcoin-cli -signet stop</code>, then run <code>bitcoin-qt -signet</code>.</span></li>
                    <li>A progress bar shows sync progress. All the <code>bitcoin-cli</code> commands below work the same whether you use the GUI or daemon.</li>
                </ul>
            </div>

            <p>To start your node in the background without a window, run the command below and press Enter:</p>
            <div style="background:#f8f9fa; padding:10px; border-radius:5px; margin:10px 0; position:relative;">
                <pre id="start-cmd" style="margin:0;">bitcoind -signet -daemon</pre>
                <button onclick="copyToClipboard('start-cmd')" style="position:absolute; top:10px; right:10px; background:#f7931a; color:white; border:none; padding:5px 10px; border-radius:3px; cursor:pointer;">Copy</button>
            </div>

            <div style="background:#f0fff4; border-left:4px solid #4caf50; border-radius:8px; padding:12px 16px; margin:10px 0;">
                <strong>What syncing looks like:</strong> When your node starts for the first time it downloads the Signet blockchain — a record of all past practice transactions. You will see a block count climbing (for example <code>"blocks": 234</code> increasing over time) — that is completely normal. Signet is much smaller than real Bitcoin so this usually finishes in a few minutes. Leave your node running while it catches up before moving to the next step.
            </div>

            <div style="background:#f0f7ff; border-left:4px solid #2196c4; border-radius:8px; padding:12px 16px; margin:10px 0;">
                <strong>How to confirm it is running:</strong> Run the command below. If you see a response including <code>"chain": "signet"</code>, your node is up and syncing.
                <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin:8px 0 0; position:relative;">
                    <pre id="check-cmd" style="margin:0; font-size:0.85rem;">bitcoin-cli -signet getblockchaininfo</pre>
                    <button onclick="copyToClipboard('check-cmd')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                </div>
            </div>

            <div style="background:#fff8e1; border-left:4px solid #f7931a; border-radius:8px; padding:12px 16px; margin:10px 0; font-size:0.9rem;">
                <strong>How to stop the node:</strong> Do not just close the terminal — the node keeps running in the background. To shut it down cleanly, run:
                <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin:8px 0 0; position:relative;">
                    <pre id="stop-cmd" style="margin:0; font-size:0.85rem;">bitcoin-cli -signet stop</pre>
                    <button onclick="copyToClipboard('stop-cmd')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                </div>
            </div>

            <p style="margin-top:28px;"><strong>Step 7 — Create a wallet</strong></p>
            <p>A wallet stores your addresses and keeps track of your test coins — think of it like opening a bank account. Run the command below and press Enter.</p>
            <div style="background:#f8f9fa; padding:10px; border-radius:5px; margin:10px 0; position:relative;">
                <pre id="wallet-cmd" style="margin:0;">bitcoin-cli -signet createwallet mywallet</pre>
                <button onclick="copyToClipboard('wallet-cmd')" style="position:absolute; top:10px; right:10px; background:#f7931a; color:white; border:none; padding:5px 10px; border-radius:3px; cursor:pointer;">Copy</button>
            </div>

            <p style="margin-top:28px;"><strong>Step 8 — Get a receive address</strong></p>
            <p>An address is like your account number — you give it to the faucet so it can send coins to you. Run the command below and copy the address that appears. It will start with <code>tb1</code>.</p>
            <div style="background:#f8f9fa; padding:10px; border-radius:5px; margin:10px 0; position:relative;">
                <pre id="addr-cmd" style="margin:0;">bitcoin-cli -signet -rpcwallet=mywallet getnewaddress</pre>
                <button onclick="copyToClipboard('addr-cmd')" style="position:absolute; top:10px; right:10px; background:#f7931a; color:white; border:none; padding:5px 10px; border-radius:3px; cursor:pointer;">Copy</button>
            </div>

            <p style="margin-top:28px;"><strong>Step 9 — Get your test coins</strong></p>
            <p><a href="#" onclick="showSection('faucetSection'); return false;">Go to the faucet →</a>, paste your address into the box, complete the code check, and click <strong>Get Coins</strong>. Your coins will appear in your wallet once the next block is mined — usually within a few minutes.</p>

            <div style="margin-top:28px;">
                <strong>&#128736; Troubleshooting — click a problem to see the fix</strong>
                <div style="margin-top:10px;">
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            "Could not connect to the server" when running bitcoin-cli <span>＋</span>
                        </button>
                        <div class="faq-answer">Your node is not running yet, or it's still starting up. Wait 30 seconds and try the command below again. If it still fails, double-check that <code>bitcoind -signet -daemon</code> ran without any error message.
                            <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin:8px 0 0; position:relative;">
                                <pre id="faq-check1" style="margin:0; font-size:0.85rem;">bitcoin-cli -signet getblockchaininfo</pre>
                                <button onclick="copyToClipboard('faq-check1')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>
                    </div>
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            "bitcoind: command not found" <span>＋</span>
                        </button>
                        <div class="faq-answer">Bitcoin's programs are not in your system path. On Windows, navigate to the folder where you installed Bitcoin and run the command from there. On Mac or Linux, you may need to move the downloaded files to <code>/usr/local/bin/</code> or run them using their full path, e.g. <code>~/bitcoin/bin/bitcoind</code>.</div>
                    </div>
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            Node is stuck at the same block count <span>＋</span>
                        </button>
                        <div class="faq-answer">It may still be connecting to peers. Give it 2–3 minutes. If it stays stuck, confirm your <code>bitcoin.conf</code> has the <code>addnode=86.104.228.47:38333</code> line — this is the peer that connects you to this Signet network. Also make sure you saved the file without a .txt extension.</div>
                    </div>
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            "Wallet not found" when creating a wallet <span>＋</span>
                        </button>
                        <div class="faq-answer">Make sure your node is fully started before running <code>createwallet</code>. Confirm it's ready by running the command below — if you see a response with <code>"chain": "signet"</code>, the node is up and wallet creation should work.
                            <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin:8px 0 0; position:relative;">
                                <pre id="faq-check2" style="margin:0; font-size:0.85rem;">bitcoin-cli -signet getblockchaininfo</pre>
                                <button onclick="copyToClipboard('faq-check2')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>
                    </div>
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            Invalid address error on the faucet <span>＋</span>
                        </button>
                        <div class="faq-answer">Make sure the address you copied starts with <code>tb1</code> — that is the format for Signet. A regular Bitcoin address starting with <code>bc1</code> will be rejected here. Run the command below again and copy the full output.
                            <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin:8px 0 0; position:relative;">
                                <pre id="faq-newaddr" style="margin:0; font-size:0.85rem;">bitcoin-cli -signet -rpcwallet=mywallet getnewaddress</pre>
                                <button onclick="copyToClipboard('faq-newaddr')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>
                    </div>
                    <div class="faq-item">
                        <button class="faq-question" onclick="toggleFaq(this)">
                            Coins not appearing in my wallet <span>＋</span>
                        </button>
                        <div class="faq-answer">Coins only appear after the next block is confirmed on the network — this usually takes a few minutes. Run the command below to check your balance. If nothing shows after 10 minutes, copy the Transaction ID from the faucet page and check it on the Signet Explorer.
                            <div style="background:#f8f9fa; padding:8px 10px; border-radius:5px; margin:8px 0 0; position:relative;">
                                <pre id="faq-balance" style="margin:0; font-size:0.85rem;">bitcoin-cli -signet -rpcwallet=mywallet getbalance</pre>
                                <button onclick="copyToClipboard('faq-balance')" style="position:absolute; top:6px; right:8px; background:#f7931a; color:white; border:none; padding:4px 8px; border-radius:3px; cursor:pointer; font-size:0.8rem;">Copy</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div style="margin-top: 28px; background: #fff3cd; border-left: 5px solid #f7931a; border-radius: 10px; padding: 16px 20px;">
                <strong>&#9888;&#65039; A note on privacy for real Bitcoin:</strong>
                <p style="margin: 10px 0 0;">On Signet everything is safe to experiment with. But when you move to real Bitcoin, running your own node means you verify transactions yourself without trusting anyone else — that is the real value. For the best privacy, pair your node with your own Electrum server (like Fulcrum or Electrs) so your wallet software doesn't leak your addresses to a third party. The Plan B Academy has courses to help you go further when you're ready.</p>
            </div>
        </div>
        <div style="text-align:center; margin-top:30px; padding-top:20px; border-top:1px solid #f0e0c8;">
            <p style="color:#aaa; font-size:0.85rem; margin-bottom:10px;">Need help or have a question?</p>
            <button style="background:transparent; border:2px solid #f7931a; color:#f7931a; border-radius:10px; padding:10px 24px; font-size:0.95rem; cursor:pointer; font-weight:bold;">
                &#9993; Contact Support
            </button>
            <div style="margin-top:18px; display:flex; flex-direction:column; align-items:center; gap:10px;">
                <a href="https://www.youtube.com/playlist?list=PLxdf8G0kzsUVxZHq3IY7ur_sM8w9TnqeN" target="_blank"
                   style="display:inline-flex; align-items:center; gap:8px; color:#f7931a; font-size:0.92rem; font-weight:bold; text-decoration:none;">
                    &#127909; Bitcoin video tutorials — YouTube playlist ↗
                </a>
                <a href="https://planb.academy/en/learn-anytime" target="_blank"
                   style="display:inline-flex; align-items:center; gap:8px; color:#f7931a; font-size:0.92rem; font-weight:bold; text-decoration:none;">
                    &#127891; Learn Bitcoin anytime — Plan B Academy ↗
                </a>
            </div>
        </div>
    </div>
    {% if success %}
    <div class="success-overlay" id="successOverlay" onclick="closePopup()">
        <div class="success-popup" id="successPopup">
            <span class="popup-emoji">🎉</span>
            <div class="popup-title">You got your Signet BTC!</div>
            <div class="popup-sub">0.001 signet BTC is on its way to your wallet.</div>
            <div class="popup-close">Click anywhere to close</div>
        </div>
    </div>
    {% endif %}

    <script>
        function closePopup() {
            const overlay = document.getElementById('successOverlay');
            if (overlay) overlay.style.display = 'none';
        }
        function launchFireworks() {
            const colors = ['#f7931a','#ff5722','#e91e63','#9c27b0','#2196f3','#4caf50','#ffeb3b','#ff9800'];
            function burst(x, y) {
                for (let i = 0; i < 14; i++) {
                    const spark = document.createElement('div');
                    spark.className = 'spark';
                    spark.style.background = colors[Math.floor(Math.random() * colors.length)];
                    spark.style.left = x + 'px';
                    spark.style.top = y + 'px';
                    document.body.appendChild(spark);
                    const angle = (i / 14) * 2 * Math.PI;
                    const dist = 60 + Math.random() * 100;
                    const tx = Math.cos(angle) * dist;
                    const ty = Math.sin(angle) * dist;
                    spark.animate([
                        { transform: 'translate(0,0) scale(1)', opacity: 1 },
                        { transform: `translate(${tx}px,${ty}px) scale(0)`, opacity: 0 }
                    ], { duration: 700 + Math.random() * 400, easing: 'ease-out', fill: 'forwards' })
                    .onfinish = () => spark.remove();
                }
            }
            let count = 0;
            const interval = setInterval(() => {
                if (count++ >= 8) { clearInterval(interval); return; }
                burst(
                    Math.random() * window.innerWidth,
                    Math.random() * window.innerHeight * 0.7
                );
            }, 350);
        }
        window.addEventListener('load', function() {
            try {
                selectOS(localStorage.getItem('preferred-os') || detectOS());
                selectNode(localStorage.getItem('preferred-node') || 'core');
            } catch(e) { selectOS('windows'); selectNode('core'); }
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    const el = document.getElementById('lnd-status');
                    if (!el) return;
                    if (data.lnd) {
                        el.innerHTML = '<span style="color:#4caf50;">⚡ Lightning node: online</span>';
                    } else {
                        el.innerHTML = '<span style="color:#e53935;">⚠ Lightning node: offline — payments may not work right now</span>';
                    }
                })
                .catch(() => {
                    const el = document.getElementById('lnd-status');
                    if (el) el.innerHTML = '<span style="color:#aaa;">⚡ Node status unknown</span>';
                });
            if (document.getElementById('successOverlay')) {
                const addrField = document.getElementById('address');
                if (addrField) { addrField.value = ''; previewAddress(''); }
                launchFireworks();
                setTimeout(closePopup, 5000);
                showSection('faucetSection');
            } else if (document.querySelector('.ln-message')) {
                showSection('lightningFaucetSection');
            } else if (document.getElementById('hintBox') || document.querySelector('.message')) {
                showSection('faucetSection');
            }
        });
        function previewInvoice(val) {
            const el = document.getElementById('invoicePreview');
            if (!el) return;
            const v = val.trim();
            if (!v) { el.style.display = 'none'; return; }
            el.style.display = 'block';
            const lower = v.toLowerCase();
            if (lower.startsWith('lnbcrt') && v.length > 20) {
                el.className = 'addr-preview ok';
                el.textContent = '✓ Looks like a valid Signet invoice';
            } else if (lower.startsWith('lnbc') && !lower.startsWith('lnbcrt')) {
                el.className = 'addr-preview bad';
                el.textContent = '⚠ This looks like a mainnet Lightning invoice (lnbc...) — Signet invoices start with lnbcrt.';
            } else if (lower.startsWith('lntb')) {
                el.className = 'addr-preview bad';
                el.textContent = '⚠ This looks like a testnet invoice (lntb...) — Signet invoices start with lnbcrt.';
            } else {
                el.className = 'addr-preview bad';
                el.textContent = '⚠ A Signet Lightning invoice should start with lnbcrt.';
            }
        }
        function previewAddress(val) {
            const el = document.getElementById('addrPreview');
            if (!el) return;
            const v = val.trim();
            if (!v) { el.style.display = 'none'; return; }
            el.style.display = 'block';
            if (v.startsWith('tb1') && v.length >= 26) {
                el.className = 'addr-preview ok';
                el.textContent = '✓ Looks good: ' + v.slice(0, 12) + '...' + v.slice(-6);
            } else if (v.startsWith('bc1')) {
                el.className = 'addr-preview bad';
                el.textContent = '⚠ This looks like a real Bitcoin address (bc1...) — please use your Signet address (tb1...) instead.';
            } else {
                el.className = 'addr-preview bad';
                el.textContent = '⚠ A Signet address should start with tb1 and be about 42 characters long.';
            }
        }
        function detectOS() {
            const ua = navigator.userAgent || '';
            if (/Win/i.test(ua)) return 'windows';
            if (/Mac/i.test(ua)) return 'macos';
            return 'linux';
        }
        function selectOS(os) {
            document.querySelectorAll('.os-tab-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.os === os);
            });
            document.querySelectorAll('[data-os]').forEach(el => {
                const isSpan = el.tagName === 'SPAN';
                el.style.display = el.dataset.os === os ? (isSpan ? 'inline' : '') : 'none';
            });
            try { localStorage.setItem('preferred-os', os); } catch(e) {}
        }
        function selectNode(node) {
            // Update content visibility
            document.querySelectorAll('[data-node]').forEach(el => {
                const isInline = ['SPAN','BUTTON'].includes(el.tagName);
                el.style.display = el.dataset.node === node ? (isInline ? 'inline' : '') : 'none';
            });
            // Highlight selection cards
            ['core','knots'].forEach(function(n) {
                var card = document.getElementById('card-' + n);
                var check = document.getElementById('check-' + n);
                if (card) card.style.border = n === node ? '2px solid #f7931a' : '2px solid #e0e0e0';
                if (card) card.style.background = n === node ? '#fffbf5' : '#f8f9fa';
                if (check) check.style.display = n === node ? 'inline' : 'none';
            });
            try { localStorage.setItem('preferred-node', node); } catch(e) {}
        }
        function toggleInfo(id) {
            const box = document.getElementById(id);
            if (box) box.style.display = box.style.display === 'block' ? 'none' : 'block';
        }
        function toggleFaq(btn) {
            const answer = btn.nextElementSibling;
            const icon = btn.querySelector('span');
            const isOpen = answer.style.display === 'block';
            answer.style.display = isOpen ? 'none' : 'block';
            icon.textContent = isOpen ? '＋' : '－';
        }
        function toggleHint() {
            const box = document.getElementById('hintBox');
            if (box) box.style.display = box.style.display === 'block' ? 'none' : 'block';
        }
        function copyToClipboard(id) {
            const text = document.getElementById(id).textContent;
            navigator.clipboard.writeText(text).then(() => {
                alert('Copied to clipboard!');
            }).catch(err => {
                console.error('Failed to copy: ', err);
            });
        }
        function hideAllSections() {
            ['homeMenu','bitcoinSection','lightningSection','lightningFaucetSection','lightningNewUserSection','faucetSection','newUserSection','sparrowSection','guideSection'].forEach(id => {
                document.getElementById(id).classList.add('hidden');
            });
        }
        function showSection(id) {
            hideAllSections();
            const el = document.getElementById(id);
            el.classList.remove('hidden');
            document.getElementById('homeIntro').style.display = (id === 'homeMenu') ? '' : 'none';
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    </script>
    <div id="lightbox">
        <span id="lightbox-close">&#x2715;</span>
        <img id="lightbox-img" src="" alt="">
    </div>
    <script>
        // Lightbox — runs after DOM is ready
        (function() {
            const lb = document.getElementById('lightbox');
            const lbImg = document.getElementById('lightbox-img');
            document.querySelectorAll('.zoomable').forEach(function(img) {
                img.addEventListener('click', function() { lbImg.src = img.src; lb.classList.add('open'); });
            });
            lb.addEventListener('click', function() { lb.classList.remove('open'); });
            document.addEventListener('keydown', function(e) { if (e.key === 'Escape') lb.classList.remove('open'); });
        })();
    </script>
</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def faucet():
    if request.method == 'POST':
        address = request.form.get('address', '').strip()
        captcha_input = request.form.get('captcha', '')
        if not address:
            session['result'] = {'message': "Please enter an address.", 'hint': "A Signet address is a string of letters and numbers that identifies your test wallet. It usually starts with 'tb1'. Follow the Setup Guide to create one.", 'success': False}
        elif not verify_captcha(captcha_input):
            session['result'] = {'message': "Incorrect code. Please try again.", 'hint': "Look at the 4-digit code displayed above the input box and type it in exactly.", 'success': False}
        else:
            rpc = get_rpc_connection()
            ip = client_ip()
            if not validate_address(rpc, address):
                session['result'] = {'message': "Invalid Signet address.", 'hint': "A valid Signet address starts with 'tb1' and is about 42 characters long. Make sure you copied it correctly from your wallet. Do not use a mainnet Bitcoin address here.", 'success': False}
            elif is_btc_rate_limited(address, ip):
                session['result'] = {'message': f"This address or IP already received coins in the last {RATE_LIMIT_HOURS} hours.", 'hint': "Each address/device can only request coins once per day. Try again tomorrow, or use a different Signet address from your wallet.", 'success': False}
            elif distributed_today('btc_requests') + btc_amount_sats() > BTC_DAILY_LIMIT_SATS:
                session['result'] = {'message': "The faucet reached today's Bitcoin distribution limit.", 'hint': "This anti-abuse ceiling resets daily. Please try again tomorrow.", 'success': False}
            else:
                try:
                    txid, error = send_coins(rpc, address)
                    if txid:
                        record_btc_request(address, ip, btc_amount_sats(), txid)
                        txid_link = f'<a href="{EXPLORER_URL.rstrip("/")}/tx/{txid}" target="_blank" style="color:#f7931a;word-break:break-all;">{txid[:10]}...{txid[-6:]} ↗</a>'
                        session['result'] = {'message': f"Sent {AMOUNT} signet BTC to {address}. Transaction ID: {txid_link}", 'hint': "Click the Transaction ID link to watch your transaction confirm in real time on the Signet Explorer. Coins appear once the next block is mined.", 'success': True}
                    else:
                        session['result'] = {'message': f"Transaction failed: {error}", 'hint': "Something went wrong while sending the coins. This can happen if the node is busy or restarting. Please try again in a few minutes.", 'success': False}
                except Exception as e:
                    session['result'] = {'message': f"Error: {str(e)}", 'hint': "An unexpected error occurred. Please try again. If the problem persists, the node may be offline or restarting.", 'success': False}
        return redirect(url_for('faucet'))

    result = session.pop('result', None)
    message = result['message'] if result else None
    hint = result['hint'] if result else None
    success = result['success'] if result else False

    ln_result = session.pop('ln_result', None)
    ln_message = ln_result['message'] if ln_result else None
    ln_hint = ln_result['hint'] if ln_result else None
    ln_success = ln_result['success'] if ln_result else False

    captcha_code = generate_captcha()
    ln_captcha_code = generate_ln_captcha()
    return render_template_string(HTML_TEMPLATE, message=message, hint=hint, success=success,
                                  captcha_code=captcha_code, ln_message=ln_message,
                                  ln_hint=ln_hint, ln_success=ln_success,
                                  ln_captcha_code=ln_captcha_code)

@app.route('/api/status')
def api_status():
    bitcoin_ok = False
    balance = None
    try:
        rpc = get_rpc_connection()
        rpc.getblockchaininfo()
        balance = float(rpc.getbalance())
        bitcoin_ok = True
    except Exception:
        pass
    lnd_ok = False
    try:
        result = subprocess.run(LNCLI_BASE + ['getinfo'], capture_output=True, text=True, timeout=5)
        lnd_ok = result.returncode == 0
    except Exception:
        pass
    return jsonify({
        'bitcoin': bitcoin_ok,
        'lnd': lnd_ok,
        'balance_btc': balance,
        'limits': {
            'rate_limit_hours': RATE_LIMIT_HOURS,
            'btc_amount': str(AMOUNT),
            'btc_daily_limit_sats': BTC_DAILY_LIMIT_SATS,
            'ln_max_sats': LN_MAX_SATS,
            'ln_daily_limit_sats': LN_DAILY_LIMIT_SATS,
        },
        'distributed_today': {
            'btc_sats': distributed_today('btc_requests'),
            'ln_sats': distributed_today('ln_requests'),
        },
        'last_drips': last_drips(),
    })

@app.route('/lightning', methods=['POST'])
def lightning_pay():
    invoice = request.form.get('ln_invoice', '').strip()
    captcha_input = request.form.get('ln_captcha', '')

    if not verify_ln_captcha(captcha_input):
        session['ln_result'] = {'message': 'Incorrect code. Please try again.', 'hint': 'Look at the 4-digit code displayed above the input box and type it exactly.', 'success': False}
        return redirect(url_for('faucet'))

    client_ip_value = client_ip()
    if is_ln_rate_limited(client_ip_value):
        session['ln_result'] = {'message': f'You already requested Lightning sats in the last {RATE_LIMIT_HOURS} hours.', 'hint': 'Each device can only request sats once per day. Try again tomorrow.', 'success': False}
        return redirect(url_for('faucet'))

    if not invoice:
        session['ln_result'] = {'message': 'Please paste a Lightning invoice.', 'hint': 'Open your Lightning wallet, go to Receive, enter an amount, and copy the invoice string.', 'success': False}
        return redirect(url_for('faucet'))

    if not invoice.lower().startswith('lnbcrt'):
        session['ln_result'] = {'message': 'Invalid invoice. This faucet only accepts Signet invoices (starting with lnbcrt).', 'hint': 'Make sure your Lightning wallet is set to Signet mode. Mainnet invoices start with lnbc and are not accepted here.', 'success': False}
        return redirect(url_for('faucet'))

    try:
        decode_proc = subprocess.run(
            LNCLI_BASE + ['decodepayreq', invoice],
            capture_output=True, text=True, timeout=10
        )
        if decode_proc.returncode != 0:
            err = decode_proc.stderr.strip() or 'Unknown error'
            session['ln_result'] = {'message': f'Invalid invoice: {err}', 'hint': 'The invoice may be expired or malformed. Generate a fresh invoice in your wallet and try again.', 'success': False}
            return redirect(url_for('faucet'))

        decoded = json.loads(decode_proc.stdout)
        num_satoshis = int(decoded.get('num_satoshis', 0))

        if num_satoshis == 0:
            session['ln_result'] = {'message': 'Please set an amount in your wallet before generating the invoice.', 'hint': 'Zero-amount invoices are not supported. Enter an amount between 1 and 10,000 sats in the Receive screen of your wallet, then copy the invoice.', 'success': False}
            return redirect(url_for('faucet'))

        if num_satoshis > LN_MAX_SATS:
            session['ln_result'] = {'message': f'Invoice amount ({num_satoshis:,} sats) exceeds the maximum of {LN_MAX_SATS:,} sats per request.', 'hint': f'Generate a new invoice for {LN_MAX_SATS:,} sats or less.', 'success': False}
            return redirect(url_for('faucet'))

        if distributed_today('ln_requests') + num_satoshis > LN_DAILY_LIMIT_SATS:
            session['ln_result'] = {'message': "The faucet reached today's Lightning distribution limit.", 'hint': 'This anti-abuse ceiling resets daily. Please try again tomorrow.', 'success': False}
            return redirect(url_for('faucet'))

        pay_proc = subprocess.run(
            LNCLI_BASE + ['payinvoice', '--force', invoice],
            capture_output=True, text=True, timeout=60
        )

        if pay_proc.returncode != 0:
            err = pay_proc.stderr.strip() or pay_proc.stdout.strip() or 'Payment failed'
            session['ln_result'] = {'message': f'Payment failed: {err}', 'hint': 'This can happen if there is no route to your wallet. Make sure your wallet is online and has an open channel to our Signet hub. Try generating a fresh invoice.', 'success': False}
            return redirect(url_for('faucet'))

        record_ln_request(client_ip_value, num_satoshis, decoded.get('payment_hash'))
        session['ln_result'] = {'message': f'Sent {num_satoshis:,} sats! Check your Lightning wallet — it should arrive instantly.', 'hint': 'If the sats do not appear, make sure your wallet app is open and online. Lightning payments are instant but require both sides to be connected.', 'success': True}

    except subprocess.TimeoutExpired:
        session['ln_result'] = {'message': 'Payment timed out. The Lightning node may be busy.', 'hint': 'Try again in a moment. If the problem persists, the node may be restarting.', 'success': False}
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        session['ln_result'] = {'message': 'Failed to decode invoice. It may be malformed.', 'hint': 'Try generating a fresh invoice in your wallet. Make sure your wallet is on the Signet network.', 'success': False}
    except Exception as e:
        session['ln_result'] = {'message': f'Error: {str(e)}', 'hint': 'An unexpected error occurred. Please try again.', 'success': False}

    return redirect(url_for('faucet'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)