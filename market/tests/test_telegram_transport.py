"""Telegram-only IPv4 dialing, TLS identity, and separate connect/read deadlines."""
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest
import requests
from django.test import override_settings
from urllib3.connection import HTTPSConnection
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util import connection as urllib_connection

from market import telegram


def test_telegram_socket_uses_ipv4_and_keeps_tls_hostname():
    from market.telegram_transport import session

    http = session()
    adapter = http.get_adapter('https://api.telegram.org/bottest/getMe')
    pool = adapter.poolmanager.connection_from_url('https://api.telegram.org')
    conn = pool._new_conn()
    conn.timeout = 3
    sock = Mock()
    resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('149.154.166.110', 443))]
    dns_before = socket.getaddrinfo
    family_before = urllib_connection.allowed_gai_family
    with patch('socket.getaddrinfo', return_value=resolved) as dns, \
         patch('urllib3.util.connection.create_connection', return_value=sock) as dial, \
         patch('urllib3.connection._ssl_wrap_socket_and_match_hostname', return_value=(sock, True)) as tls:
        conn.connect()
    assert dns.call_args.args[:3] == ('api.telegram.org', 443, socket.AF_INET)
    assert dial.call_args.args[0] == ('149.154.166.110', 443)
    assert tls.call_args.kwargs['server_hostname'] == 'api.telegram.org'
    assert tls.call_args.kwargs['cert_reqs'] == ssl.CERT_REQUIRED
    assert conn.host == 'api.telegram.org'
    assert conn.is_verified
    assert socket.getaddrinfo is dns_before
    assert urllib_connection.allowed_gai_family is family_before
    # An unrelated Requests session keeps its ordinary dual-stack connection class.
    other = requests.Session().get_adapter('https://example.test').poolmanager
    assert other.connection_from_url('https://example.test').ConnectionCls is HTTPSConnection


def test_ipv4_dial_tries_other_addresses_after_failed_connect():
    from market.telegram_transport import IPv4HTTPSConnection

    sock = Mock()
    resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))
                for ip in ('192.0.2.1', '192.0.2.2')]
    conn = IPv4HTTPSConnection('api.telegram.org', timeout=3)
    with patch('socket.getaddrinfo', return_value=resolved), \
         patch('urllib3.util.connection.create_connection', side_effect=[OSError('unreachable'), sock]) as dial:
        assert conn._new_conn() is sock
    assert [call.args[0][0] for call in dial.call_args_list] == ['192.0.2.1', '192.0.2.2']


@pytest.mark.parametrize('failure,expected', [
    (socket.gaierror('no A record'), NewConnectionError),
    (socket.timeout('connect stalled'), ConnectTimeoutError),
    (OSError('unreachable'), NewConnectionError),
])
def test_dial_errors_are_wrapped_for_requests(failure, expected):
    from market.telegram_transport import IPv4HTTPSConnection

    conn = IPv4HTTPSConnection('api.telegram.org', timeout=3)
    with patch('socket.getaddrinfo', side_effect=failure), pytest.raises(expected):
        conn._new_conn()


def test_sessions_reuse_connections_without_sharing_between_threads():
    from market.telegram_transport import session

    first = session()
    assert session() is first
    with ThreadPoolExecutor(max_workers=1) as executor:
        other = executor.submit(session).result()
        assert executor.submit(session).result() is other
    assert other is not first
    retries = first.get_adapter('https://api.telegram.org/').max_retries
    assert retries.total == 0


@override_settings(TELEGRAM_BOT_TOKEN='test-token')
@pytest.mark.parametrize('http_timeout,expected', [(40, (3, 40)), (5, (3, 5)), (1, (1, 1))])
def test_getupdates_keeps_long_poll_read_deadline(http_timeout, expected):
    response = Mock(ok=True)
    response.json.return_value = {'ok': True, 'result': []}
    with patch('requests.Session.request', return_value=response) as request:
        assert telegram.api('getUpdates', timeout=25, http_timeout=http_timeout) == []
    assert request.call_args.kwargs['timeout'] == expected
    assert request.call_args.kwargs['json']['timeout'] == 25


@override_settings(TELEGRAM_BOT_TOKEN='test-token')
def test_upload_and_download_use_ipv4_transport_and_bounded_connect_timeout():
    from market.telegram_transport import session

    response = Mock(ok=True, content=b'image')
    response.json.return_value = {'ok': True, 'result': {'message_id': 1}}
    with patch.object(session(), 'request', return_value=response) as request:
        assert telegram.upload('sendPhoto', {'photo': ('image.jpg', b'image')}, chat_id=1) == {'message_id': 1}
    assert request.call_args.kwargs['timeout'] == (3, 60)
    with patch('market.telegram.api', return_value={'file_path': 'photos/image.jpg'}), \
         patch.object(session(), 'request', return_value=response) as request:
        assert telegram.download_file('file-id') == (b'image', 'photos/image.jpg')
    assert request.call_args.kwargs['timeout'] == (3, 40)


@override_settings(TELEGRAM_BOT_TOKEN='secret-token')
@pytest.mark.parametrize('error', [requests.ConnectTimeout('secret-token'), requests.ReadTimeout('secret-token')])
def test_network_errors_do_not_expose_token_or_retry_send(error):
    with patch('requests.Session.request', side_effect=error) as request:
        with pytest.raises(telegram.TelegramError) as exc:
            telegram.send(1, 'text')
    assert 'secret-token' not in str(exc.value)
    assert request.call_count == 1


@override_settings(TELEGRAM_BOT_TOKEN='test-token')
def test_failed_post_connection_is_not_automatically_retried():
    resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443))]
    with patch('socket.getaddrinfo', return_value=resolved), \
         patch('urllib3.util.connection.create_connection', side_effect=socket.timeout()) as dial:
        with pytest.raises(telegram.TelegramError):
            telegram.send(1, 'text')
    assert dial.call_count == 1


def test_forked_process_does_not_reuse_parent_connection_pool():
    from market.telegram_transport import session

    parent = session()
    with patch('market.telegram_transport.os.getpid', return_value=-1), patch.object(parent, 'close') as close:
        child = session()
        assert child is not parent
        assert session() is child
    close.assert_called_once()


def test_telegram_session_does_not_use_environment_proxy(monkeypatch):
    from market.telegram_transport import session

    monkeypatch.setenv('HTTPS_PROXY', 'http://unrelated.test:8080')
    http = session()
    env = http.merge_environment_settings('https://api.telegram.org/bottest/getMe', {}, None, None, None)
    assert env['proxies'] == {}
