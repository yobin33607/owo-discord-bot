#!/usr/bin/env python3
"""
Limey Proxy Server
==================
A lightweight SOCKS5 + HTTP CONNECT proxy server.

Use it as a local proxy for the bot. Supports optional
username/password authentication and connection logging.

Usage:
    python proxy_server.py --port 1080
    python proxy_server.py --port 1080 --auth user:pass
    python proxy_server.py --port 3128 --mode http
    python proxy_server.py --help
"""

import argparse
import asyncio
import logging
import os
import signal
import socket
import struct
import sys
from datetime import datetime

# ─────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────

VERSION = "1.0.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("proxy")


# ─────────────────────────────────────────────────────────
#  SOCKS5 Protocol Implementation
# ─────────────────────────────────────────────────────────

SOCKS5_VERSION = 0x05

# Auth methods
AUTH_NO_AUTH = 0x00
AUTH_USER_PASS = 0x02
AUTH_NO_ACCEPTABLE = 0xFF

# Commands
CMD_CONNECT = 0x01

# Address types
ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

# Replies
REPLY_SUCCESS = 0x00
REPLY_GENERAL_FAILURE = 0x01
REPLY_NOT_ALLOWED = 0x02
REPLY_NETWORK_UNREACHABLE = 0x03
REPLY_HOST_UNREACHABLE = 0x04
REPLY_CONNECTION_REFUSED = 0x05
REPLY_TTL_EXPIRED = 0x06
REPLY_COMMAND_NOT_SUPPORTED = 0x07
REPLY_ADDRESS_NOT_SUPPORTED = 0x08


class SOCKS5Proxy:
    """SOCKS5 proxy connection handler."""

    def __init__(self, reader, writer, auth_required=False, username="", password=""):
        self.reader = reader
        self.writer = writer
        self.auth_required = auth_required
        self.username = username
        self.password = password
        self.peer = writer.get_extra_info("peername") or ("unknown", 0)

    async def handle(self):
        try:
            await self._handshake()
            request = await self._read_request()
            if request["cmd"] == CMD_CONNECT:
                await self._handle_connect(request)
            else:
                await self._send_reply(REPLY_COMMAND_NOT_SUPPORTED)
        except (ConnectionError, asyncio.TimeoutError, OSError) as e:
            log.debug(f"[{self.peer[0]}:{self.peer[1]}] Connection error: {e}")
        finally:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass

    async def _read_exactly(self, n):
        data = await self.reader.readexactly(n)
        return data

    async def _handshake(self):
        # Read greeting: VER, NMETHODS, METHODS
        data = await self._read_exactly(2)
        ver, nmethods = struct.unpack("!BB", data)

        if ver != SOCKS5_VERSION:
            raise ConnectionError(f"Invalid SOCKS version: {ver}")

        methods = await self._read_exactly(nmethods)

        if self.auth_required:
            if AUTH_USER_PASS in methods:
                # Send auth required
                self.writer.write(struct.pack("!BB", SOCKS5_VERSION, AUTH_USER_PASS))
                await self.writer.drain()

                # Read auth: VER, ULEN, UNAME, PLEN, PASSWD
                auth_data = await self._read_exactly(2)
                aver, ulen = struct.unpack("!BB", auth_data)
                if aver != 0x01:
                    raise ConnectionError(f"Invalid auth version: {aver}")

                uname = await self._read_exactly(ulen)
                plen_data = await self._read_exactly(1)
                plen = plen_data[0]
                passwd = await self._read_exactly(plen)

                if uname.decode("utf-8", errors="replace") == self.username and \
                   passwd.decode("utf-8", errors="replace") == self.password:
                    self.writer.write(struct.pack("!BB", 0x01, 0x00))  # Success
                    await self.writer.drain()
                else:
                    self.writer.write(struct.pack("!BB", 0x01, 0x01))  # Failure
                    await self.writer.drain()
                    raise PermissionError("Authentication failed")
            else:
                self.writer.write(struct.pack("!BB", SOCKS5_VERSION, AUTH_NO_ACCEPTABLE))
                await self.writer.drain()
                raise ConnectionError("No acceptable auth method")
        else:
            if AUTH_NO_AUTH in methods:
                self.writer.write(struct.pack("!BB", SOCKS5_VERSION, AUTH_NO_AUTH))
                await self.writer.drain()
            else:
                self.writer.write(struct.pack("!BB", SOCKS5_VERSION, AUTH_NO_ACCEPTABLE))
                await self.writer.drain()
                raise ConnectionError("No acceptable auth method")

    async def _read_request(self):
        # VER, CMD, RSV, ATYP
        data = await self._read_exactly(4)
        ver, cmd, rsv, atyp = struct.unpack("!BBBB", data)

        if atyp == ATYP_IPV4:
            addr_data = await self._read_exactly(4)
            addr = socket.inet_ntop(socket.AF_INET, addr_data)
        elif atyp == ATYP_DOMAIN:
            length = (await self._read_exactly(1))[0]
            addr = (await self._read_exactly(length)).decode("utf-8", errors="replace")
        elif atyp == ATYP_IPV6:
            addr_data = await self._read_exactly(16)
            addr = socket.inet_ntop(socket.AF_INET6, addr_data)
        else:
            raise ConnectionError(f"Unsupported address type: {atyp}")

        port_data = await self._read_exactly(2)
        port = struct.unpack("!H", port_data)[0]

        return {"ver": ver, "cmd": cmd, "atyp": atyp, "addr": addr, "port": port}

    async def _send_reply(self, reply_code, bind_addr="0.0.0.0", bind_port=0):
        try:
            bind_addr_bytes = socket.inet_pton(socket.AF_INET, bind_addr)
            atyp = ATYP_IPV4
        except OSError:
            try:
                bind_addr_bytes = socket.inet_pton(socket.AF_INET6, bind_addr)
                atyp = ATYP_IPV6
            except OSError:
                bind_addr_bytes = bind_addr.encode()
                atyp = ATYP_DOMAIN

        self.writer.write(struct.pack("!BBBB", SOCKS5_VERSION, reply_code, 0x00, atyp))
        if atyp == ATYP_DOMAIN:
            self.writer.write(struct.pack("!B", len(bind_addr_bytes)))
        self.writer.write(bind_addr_bytes)
        self.writer.write(struct.pack("!H", bind_port))
        await self.writer.drain()

    async def _handle_connect(self, request):
        target_host = request["addr"]
        target_port = request["port"]

        log.info(f"CONNECT {target_host}:{target_port}  ←  {self.peer[0]}:{self.peer[1]}")

        try:
            target_reader, target_writer = await asyncio.wait_for(
                asyncio.open_connection(target_host, target_port),
                timeout=15,
            )
        except asyncio.TimeoutError:
            log.warning(f"TIMEOUT {target_host}:{target_port}")
            await self._send_reply(REPLY_TTL_EXPIRED)
            return
        except (OSError, ConnectionError) as e:
            log.warning(f"REFUSED {target_host}:{target_port}  ({e})")
            await self._send_reply(REPLY_HOST_UNREACHABLE)
            return

        # Determine local bind address for the response
        local_addr = target_writer.get_extra_info("sockname") or ("0.0.0.0", 0)
        await self._send_reply(REPLY_SUCCESS, bind_addr=local_addr[0], bind_port=local_addr[1])

        async def pipe(r, w, name):
            try:
                while True:
                    data = await r.read(65536)
                    if not data:
                        break
                    w.write(data)
                    await w.drain()
            except Exception:
                pass
            finally:
                try:
                    w.close()
                except Exception:
                    pass

        await asyncio.gather(
            pipe(self.reader, target_writer, "C→T"),
            pipe(target_reader, self.writer, "T→C"),
        )

        log.info(f"CLOSE   {target_host}:{target_port}  ←  {self.peer[0]}:{self.peer[1]}")


# ─────────────────────────────────────────────────────────
#  HTTP CONNECT Proxy
# ─────────────────────────────────────────────────────────

class HTTPProxy:
    """HTTP CONNECT proxy handler — for tools that only speak HTTP proxy."""

    def __init__(self, reader, writer, auth_required=False, username="", password=""):
        self.reader = reader
        self.writer = writer
        self.auth_required = auth_required
        self.username = username
        self.password = password
        self.peer = writer.get_extra_info("peername") or ("unknown", 0)

    async def handle(self):
        try:
            # Read the CONNECT request line
            request_line = await self._read_line()
            if not request_line:
                return

            parts = request_line.split()
            if len(parts) < 3:
                await self._write_error(400)
                return

            method = parts[0].upper()
            target = parts[1]
            http_version = parts[2]

            # Read headers
            headers = {}
            while True:
                line = await self._read_line()
                if not line:
                    break
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()

            # Check authentication if required
            if self.auth_required:
                if not self._check_auth(headers):
                    await self._write_response(
                        "407 Proxy Authentication Required",
                        {"Proxy-Authenticate": "Basic realm=\"Limey Proxy\""},
                    )
                    return

            if method == "CONNECT":
                # CONNECT host:port HTTP/1.1
                host_port = target
                if ":" in host_port:
                    host, port_str = host_port.rsplit(":", 1)
                    try:
                        port = int(port_str)
                    except ValueError:
                        await self._write_error(400)
                        return
                else:
                    port = 443

                log.info(f"CONNECT {host}:{port}  ←  {self.peer[0]}:{self.peer[1]}")

                try:
                    target_reader, target_writer = await asyncio.wait_for(
                        asyncio.open_connection(host, port),
                        timeout=15,
                    )
                except (asyncio.TimeoutError, OSError, ConnectionError) as e:
                    log.warning(f"FAIL {host}:{port}  ({e})")
                    await self._write_error(502)
                    return

                await self._write_response("200 Connection Established")

                async def pipe(r, w, name):
                    try:
                        while True:
                            data = await r.read(65536)
                            if not data:
                                break
                            w.write(data)
                            await w.drain()
                    except Exception:
                        pass
                    finally:
                        try:
                            w.close()
                        except Exception:
                            pass

                await asyncio.gather(
                    pipe(self.reader, target_writer, "C→T"),
                    pipe(target_reader, self.writer, "T→C"),
                )

                log.info(f"CLOSE   {host}:{port}  ←  {self.peer[0]}:{self.peer[1]}")
            else:
                # For non-CONNECT methods, just refuse (this is a tunneling proxy)
                await self._write_error(405)
        except Exception as e:
            log.debug(f"HTTP proxy error: {e}")
        finally:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass

    def _check_auth(self, headers):
        auth = headers.get("proxy-authorization", "")
        if not auth.startswith("Basic "):
            return False
        import base64
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            user, passwd = decoded.split(":", 1)
            return user == self.username and passwd == self.password
        except Exception:
            return False

    async def _read_line(self):
        data = []
        while True:
            ch = await self.reader.read(1)
            if not ch:
                return None if not data else b"".join(data).decode("utf-8", errors="replace").strip()
            if ch == b"\n":
                break
            data.append(ch)
        line = b"".join(data).decode("utf-8", errors="replace").strip("\r").strip()
        return line

    async def _write_response(self, status, extra_headers=None):
        resp = f"HTTP/1.1 {status}\r\n"
        if extra_headers:
            for k, v in extra_headers.items():
                resp += f"{k}: {v}\r\n"
        resp += "\r\n"
        self.writer.write(resp.encode())
        await self.writer.drain()

    async def _write_error(self, code):
        messages = {400: "Bad Request", 405: "Method Not Allowed", 407: "Proxy Authentication Required", 502: "Bad Gateway"}
        msg = messages.get(code, "Error")
        await self._write_response(f"{code} {msg}")
        log.warning(f"HTTP {code}  ←  {self.peer[0]}:{self.peer[1]}")


# ─────────────────────────────────────────────────────────
#  Server
# ─────────────────────────────────────────────────────────

class ProxyServer:
    """Main proxy server that accepts connections and dispatches them."""

    def __init__(self, host="0.0.0.0", port=1080, mode="socks5",
                 auth_required=False, username="", password="", max_conns=100):
        self.host = host
        self.port = port
        self.mode = mode.lower()
        self.auth_required = auth_required
        self.username = username
        self.password = password
        self.max_conns = max_conns
        self.server = None
        self.semaphore = asyncio.Semaphore(max_conns)

    async def start(self):
        self.server = await asyncio.start_server(
            self._on_connect,
            host=self.host,
            port=self.port,
            backlog=128,
        )

        addrs = ", ".join(str(s.getsockname()) for s in self.server.sockets)
        mode_label = "SOCKS5" if self.mode == "socks5" else "HTTP CONNECT"
        auth_label = " (auth: " + self.username + ")" if self.auth_required else " (no auth)"
        print("")
        print(f"  ╔══ Limey Proxy Server v{VERSION} ═══════════════╗")
        print(f"  ║  Mode:    {mode_label:<25s}  ║")
        print(f"  ║  Listen:  {self.host}:{self.port:<18s}  ║")
        print(f"  ║  Auth:    {'enabled' + auth_label:<25s}  ║" if self.auth_required else f"  ║  Auth:    disabled{'':24s}  ║")
        print(f"  ║  Max conns: {self.max_conns:<5d}{'':19s}  ║")
        print(f"  ╚══════════════════════════════════════════╝")
        print(f"  Listening on {addrs}")
        print(f"  Add to your bot's proxies: {self.host if self.host != '0.0.0.0' else '127.0.0.1'}:{self.port}")
        print("")

        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _on_connect(self, reader, writer):
        async with self.semaphore:
            if self.mode == "socks5":
                handler = SOCKS5Proxy(reader, writer, self.auth_required, self.username, self.password)
            else:
                handler = HTTPProxy(reader, writer, self.auth_required, self.username, self.password)
            await handler.handle()


# ─────────────────────────────────────────────────────────
#  CLI Entry Point
# ─────────────────────────────────────────────────────────

def parse_auth(auth_str):
    """Parse 'user:pass' into (username, password)."""
    if not auth_str:
        return None, None
    if ":" not in auth_str:
        print("  [!] Auth must be in format: username:password")
        sys.exit(1)
    user, passwd = auth_str.split(":", 1)
    if not user or not passwd:
        print("  [!] Username and password cannot be empty")
        sys.exit(1)
    return user, passwd


def main():
    parser = argparse.ArgumentParser(
        description="Limey Proxy Server — SOCKS5 & HTTP CONNECT proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python proxy_server.py                          # SOCKS5 on :1080, no auth
  python proxy_server.py --port 3128 --mode http  # HTTP CONNECT on :3128
  python proxy_server.py --auth user123:pass456   # SOCKS5 with auth on :1080
  python proxy_server.py --verbose                # Verbose logging
        """,
    )
    parser.add_argument("--port", type=int, default=1080, help="Port to listen on (default: 1080)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--mode", choices=["socks5", "http"], default="socks5",
                        help="Proxy mode: socks5 or http (default: socks5)")
    parser.add_argument("--auth", default="", help="Username:password for proxy authentication")
    parser.add_argument("--max-conns", type=int, default=100, help="Max concurrent connections (default: 100)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose debug logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger("proxy").setLevel(logging.DEBUG)
        log.debug("Verbose logging enabled")

    username, password = parse_auth(args.auth)
    auth_required = bool(username)

    server = ProxyServer(
        host=args.host,
        port=args.port,
        mode=args.mode,
        auth_required=auth_required,
        username=username or "",
        password=password or "",
        max_conns=args.max_conns,
    )

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler():
        print("\n  [!] Shutting down...")
        shutdown_event.set()

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        # On Windows, handle Ctrl+C differently
        if sys.platform == "win32":
            try:
                loop.run_until_complete(server.start())
            except KeyboardInterrupt:
                signal_handler()
        else:
            loop.run_until_complete(
                asyncio.gather(
                    server.start(),
                    asyncio.sleep(999999),  # Run until signal
                )
            )
    except asyncio.CancelledError:
        pass
    finally:
        loop.run_until_complete(server.stop())
        loop.close()
        print("  [✓] Proxy server stopped")


if __name__ == "__main__":
    main()
