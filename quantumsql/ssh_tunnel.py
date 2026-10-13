"""SSH tunnels (paramiko): reach a MySQL server that is only listening on the SSH host's private network."""
import os
import select
import socket
import threading

_tunnels = {}
_lock = threading.Lock()


class SshTunnel:
    """Local port forward 127.0.0.1:<random> → (remote_host, remote_port) through an SSH server."""

    def __init__(self, ssh_host, ssh_port, ssh_user, password="", key_file="", key_pass="", remote_host="127.0.0.1",
                 remote_port=3306, timeout=10):
        self.args = (ssh_host, int(ssh_port or 22), ssh_user, password, key_file, key_pass)
        self.remote = (remote_host or "127.0.0.1", int(remote_port or 3306))
        self.timeout = timeout
        self.transport = None
        self.server = None
        self.local_port = None
        self._stop = threading.Event()

    def start(self):
        import paramiko
        host, port, user, password, key_file, key_pass = self.args
        from .config import data_dir
        client = paramiko.SSHClient()
        # Trust on first use: remember each server's host key and refuse the connection if it later changes
        # (paramiko raises BadHostKeyException) — that is what a man-in-the-middle would look like.
        known_hosts = os.path.join(data_dir(), "known_hosts")
        try:
            client.load_system_host_keys()
        except OSError:
            pass
        if not os.path.exists(known_hosts):
            open(known_hosts, "a").close()
        client.load_host_keys(known_hosts)
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {"hostname": host, "port": port, "username": user, "timeout": self.timeout,
                  "banner_timeout": self.timeout, "auth_timeout": self.timeout, "allow_agent": not password,
                  "look_for_keys": not (password or key_file)}
        if key_file:
            kwargs["key_filename"] = key_file
            if key_pass:
                kwargs["passphrase"] = key_pass
        if password:
            kwargs["password"] = password
        try:
            client.connect(**kwargs)
        except paramiko.BadHostKeyException as exc:
            raise ConnectionError(
                f"SSH host key for {host} has CHANGED — connection refused. This can mean someone is intercepting "
                f"the connection. If the server was reinstalled on purpose, remove its line from {known_hosts}."
            ) from exc
        client.save_host_keys(known_hosts)
        self.client = client
        self.transport = client.get_transport()
        self.transport.set_keepalive(30)
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(16)
        self.local_port = self.server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True, name="ssh-tunnel-accept").start()
        return self.local_port

    def alive(self):
        return bool(self.transport and self.transport.is_active() and not self._stop.is_set())

    def _accept(self):
        while not self._stop.is_set():
            try:
                sock, peer = self.server.accept()
            except OSError:
                break
            try:
                chan = self.transport.open_channel("direct-tcpip", self.remote, peer)
            except Exception:  # noqa: BLE001 - refuse this client, keep listening
                sock.close()
                continue
            threading.Thread(target=self._pump, args=(sock, chan), daemon=True, name="ssh-tunnel-pump").start()

    def _pump(self, sock, chan):
        try:
            while not self._stop.is_set():
                r, _, _ = select.select([sock, chan], [], [], 1.0)
                if sock in r:
                    data = sock.recv(65536)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(65536)
                    if not data:
                        break
                    sock.sendall(data)
        except (OSError, EOFError):
            pass
        finally:
            for c in (chan, sock):
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass

    def stop(self):
        self._stop.set()
        for c in (self.server, getattr(self, "client", None)):
            try:
                if c:
                    c.close()
            except Exception:  # noqa: BLE001
                pass


def tunnel_for(params):
    """Return (host, port) to connect pymysql to, opening or reusing the session's SSH tunnel."""
    if not params.get("ssh_enabled"):
        return params.get("host") or "127.0.0.1", int(params.get("port") or 3306)
    key = (params.get("ssh_host"), int(params.get("ssh_port") or 22), params.get("ssh_user"),
           params.get("ssh_key_file") or "", params.get("host") or "127.0.0.1", int(params.get("port") or 3306))
    with _lock:
        t = _tunnels.get(key)
        if t is None or not t.alive():
            if t is not None:
                t.stop()
            t = SshTunnel(params.get("ssh_host"), params.get("ssh_port"), params.get("ssh_user"),
                          params.get("ssh_password") or "", params.get("ssh_key_file") or "",
                          params.get("ssh_key_pass") or "", params.get("host"), params.get("port"),
                          int(params.get("timeout") or 10))
            t.start()
            _tunnels[key] = t
        return "127.0.0.1", t.local_port


def close_all():
    with _lock:
        for t in _tunnels.values():
            t.stop()
        _tunnels.clear()
