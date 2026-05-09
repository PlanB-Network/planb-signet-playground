#!/usr/bin/env python3
"""
Forwards 127.0.0.1:38332 → ::1:38332 for Sparrow to reach the Signet node.
Also patches "initialblockdownload":true → false so Sparrow connects when the
chain is fully synced but the tip is older than Bitcoin Core's 24-hour window.
"""
import socket, threading, re

IBD_PATCH = (b'"initialblockdownload":true', b'"initialblockdownload":false')

def patch_response(data):
    """Patch IBD flag and update Content-Length if body changed."""
    if IBD_PATCH[0] not in data:
        return data
    patched = data.replace(IBD_PATCH[0], IBD_PATCH[1])
    # Body grew by 1 byte — fix Content-Length header
    old_cl = re.search(rb'Content-Length: (\d+)', data)
    if old_cl:
        new_len = int(old_cl.group(1)) + 1
        patched = patched.replace(old_cl.group(0), b'Content-Length: ' + str(new_len).encode())
    return patched

def pipe_to_client(src, dst):
    """Pipe from node to Sparrow, patching responses."""
    try:
        buf = b''
        while True:
            chunk = src.recv(4096)
            if not chunk:
                break
            buf += chunk
            # Wait until we have headers to know if patch is needed
            if b'\r\n\r\n' in buf:
                break
        if buf:
            dst.sendall(patch_response(buf))
        # Stream remaining data unmodified
        while True:
            chunk = src.recv(4096)
            if not chunk:
                break
            dst.sendall(chunk)
    except:
        pass
    finally:
        try: src.close()
        except: pass
        try: dst.close()
        except: pass

def pipe(src, dst):
    """Simple bidirectional pipe (for client→node direction)."""
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except:
        pass
    finally:
        try: src.close()
        except: pass
        try: dst.close()
        except: pass

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('127.0.0.1', 38332))
server.listen(20)
print("Proxy listening on 127.0.0.1:38332 → ::1:38332 (IBD patch active)", flush=True)

def handle(client):
    import time
    for attempt in range(10):
        try:
            target = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            target.connect(('::1', 38332))
            threading.Thread(target=pipe, args=(client, target), daemon=True).start()
            threading.Thread(target=pipe_to_client, args=(target, client), daemon=True).start()
            return
        except OSError:
            target.close()
            time.sleep(1)
    client.close()

while True:
    client, _ = server.accept()
    threading.Thread(target=handle, args=(client,), daemon=True).start()
