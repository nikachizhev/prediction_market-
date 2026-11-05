"""Direct, connection-pooled IPv4 transport for Telegram only.

The VPS intermittently stalls on Telegram's IPv6 route. Resolve A records when
opening this adapter's sockets; keep the hostname for HTTPS verification and SNI.
No process-global socket or urllib3 settings are changed.
"""
import os
import socket
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util import connection


class IPv4HTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        try:
            addresses = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)
            error = OSError("No IPv4 address for Telegram")
            for _, _, _, _, address in addresses:
                try:
                    # Numeric IPv4 address cannot resolve to IPv6. urllib3 still
                    # owns socket options, binding, timeout and failed-socket cleanup.
                    return connection.create_connection(
                        address, self.timeout, source_address=self.source_address,
                        socket_options=self.socket_options,
                    )
                except OSError as exc:
                    error = exc
            raise error
        except socket.timeout as exc:
            raise ConnectTimeoutError(self, "Telegram connection timed out") from exc
        except OSError as exc:
            raise NewConnectionError(self, "Could not connect to Telegram") from exc


class IPv4HTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = IPv4HTTPSConnection


class TelegramAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        # PoolManager normally shares this dict with every other manager.
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme,
            "https": IPv4HTTPSConnectionPool,
        }


_local = threading.local()


def session() -> requests.Session:
    """Reuse keep-alive connections per thread, never sockets inherited by fork."""
    pid = os.getpid()
    if getattr(_local, "pid", None) != pid:
        old = getattr(_local, "http", None)
        if old is not None:
            old.close()
        http = requests.Session()
        # Telegram uses a direct IPv4 connection, not environment proxies/netrc.
        # Otherwise a proxy can bypass this adapter's IPv4 route selection.
        http.trust_env = False
        http.mount("https://api.telegram.org/", TelegramAdapter(max_retries=0))
        _local.http = http
        _local.pid = pid
    return _local.http
