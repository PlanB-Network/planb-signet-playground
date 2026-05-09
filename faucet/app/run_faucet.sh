#!/bin/bash
# Starts the web faucet detached from any shell session
pkill -f web_faucet.py 2>/dev/null
sleep 1
nohup python3 /home/gabor/web_faucet.py > /home/gabor/faucet.log 2>&1 &
echo "Faucet started, PID $!"
sleep 3
ss -tlnp | grep 5000 && echo "OK - listening on :5000" || echo "ERROR - not listening"
