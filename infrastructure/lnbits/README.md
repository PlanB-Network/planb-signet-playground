# LNbits Installation & Configuration Guide

## 1. Installation

First, access Alice LND node server:

```bash
ssh alice@<host>
```

Run the official installation script:
> **Reference:** [LNbits Install Script Documentation](https://docs.lnbits.com/guide/installation/install-script)

```bash
wget https://raw.githubusercontent.com/lnbits/lnbits/main/lnbits.sh && chmod +x lnbits.sh && ./lnbits.sh
```

---

## 2. Running LNbits

Refresh your environment to enable uv:

```bash
source ~/.bashrc
```

### Development & Testing
Use this method to run LNbits in the **foreground** and view real-time logs:

```bash
cd ~/lnbits && uv run lnbits
```

### Production Environment
Use this method to run LNbits in the **background**, allowing it to persist after you disconnect:

```bash
cd ~/lnbits
nohup uv run lnbits > lnbits.log 2>&1 &
```

---

## 3. Reverse Proxy Configuration (Caddy)

To serve LNbits over **HTTPS** using your custom domain, update your **Caddyfile**.

1.  **Open the configuration file**:
    ```bash
    sudo vim /etc/caddy/Caddyfile
    ```
2.  **Add the proxy block**:
    ```caddy
    lnbits-signet.planb.academy {
        reverse_proxy 127.0.0.1:5000
    }
    ```
3.  **Reload the service** to apply changes:
    ```bash
    sudo systemctl reload caddy
    ```

---

## 4. SSH Tunnel (Local Access)

If you have not yet configured a public domain, you can access the interface securely through an **SSH tunnel** from your local machine:

```bash
# Execute this on your local computer
ssh -L 5000:127.0.0.1:5000 alice@<host>
```

After connecting, open your browser and navigate to [http://localhost:5000](http://localhost:5000).

---

## 5. Connect a Lightning Backend

Configure your funding source by navigating to **Settings -> Funding** within the **LNbits Admin UI**.

> **Reference:** [First Setup Guide](https://docs.lnbits.com/guide/installation/first-setup)

### Funding Sources Configuration:

| Parameter | Value |
| :--- | :--- |
| **Funding Source** | Lightning Network Daemon (LND) |
| **Endpoint** | `https://127.0.0.1` |
| **Certificate** | `/home/alice/.lnd-signet/tls.cert` |
| **Port** | `10020` |
| **GRPC Admin Macaroon** | `/home/alice/.lnd-signet/data/chain/bitcoin/signet/admin.macaroon` |


### Wallets Management
Once the backend is connected, you can verify your balance under **Funding -> Wallets Management**:

**Funding Source Information**
* **Node Balance:** 449,996,479 sats