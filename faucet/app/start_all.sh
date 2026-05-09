#!/bin/bash
# Starts the Bitcoin Signet node, RPC proxy, LND, and web faucet

echo "=== Starting Bitcoin Signet stack ==="

# 1. RPC proxy (must start before bitcoind to own 127.0.0.1:38332)
if pgrep -f "rpc_proxy.py" > /dev/null; then
    echo "[OK] RPC proxy already running"
else
    echo "[..] Starting RPC proxy..."
    nohup python3 /home/gabor/rpc_proxy.py > /home/gabor/rpc_proxy.log 2>&1 &
    sleep 1
    echo "[OK] RPC proxy started"
fi

# 2. Bitcoin node
if pgrep -x bitcoind > /dev/null; then
    echo "[OK] bitcoind already running"
else
    echo "[..] Starting bitcoind..."
    bitcoind -signet -daemon
    sleep 5
    echo "[OK] bitcoind started"
fi

# 3. LND
if pgrep -x lnd > /dev/null; then
    echo "[OK] LND already running"
else
    echo "[..] Starting LND..."
    nohup lnd --lnddir=/home/gabor/.lnd-signet > /home/gabor/lnd.log 2>&1 &
    sleep 4
    echo "[OK] LND started — unlock it with:"
    echo "     lncli --lnddir=/home/gabor/.lnd-signet --rpcserver=127.0.0.1:10010 unlock"
fi

# 4. Web faucet
if pgrep -f "web_faucet.py" > /dev/null; then
    echo "[OK] Web faucet already running"
else
    echo "[..] Starting web faucet..."
    systemd-run --user --unit=web-faucet python3 /home/gabor/web_faucet.py
    sleep 1
    echo "[OK] Web faucet started at http://127.0.0.1:5000"
fi

echo ""
echo "=== All services started ==="
echo "Remember to unlock LND if it was just started."
