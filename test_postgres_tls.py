"""Production trust selection without opening a database connection."""
import hashlib
import re
import ssl
from pathlib import Path
from unittest.mock import Mock

import psycopg_pool
import pytest

import secure_backend as secure


POOLER = 'aws-0-ap-northeast-1.pooler.supabase.com'


@pytest.mark.parametrize('conninfo', [
    'host=database.example.org',
    'host=127.0.0.1',
    'host=' + POOLER + '.example.org',
    'host=' + POOLER + ',database.example.org',
    'dbname=fixture',
])
def test_other_hosts_keep_system_trust(monkeypatch, conninfo):
    monkeypatch.delenv('PGSSLROOTCERT', raising=False)
    monkeypatch.delenv('PGHOST', raising=False)
    assert secure.postgres_root_certificate(conninfo) == 'system'


def test_operator_ca_settings_take_precedence(monkeypatch):
    uri = 'postgresql://' + POOLER + '/fixture?sslrootcert=/fixture/one.crt'
    monkeypatch.delenv('PGSSLROOTCERT', raising=False)
    assert secure.postgres_root_certificate(uri) == '/fixture/one.crt'
    monkeypatch.setenv('PGSSLROOTCERT', '/fixture/two.crt')
    assert secure.postgres_root_certificate(uri) == '/fixture/two.crt'


def test_production_pool_verifies_peer_with_vendor_roots(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(psycopg_pool, 'ConnectionPool', factory)
    monkeypatch.setattr(secure, '_pool', None)
    monkeypatch.setattr(secure, 'PRODUCTION', True)
    monkeypatch.setattr(secure, 'DATABASE_URL', 'postgresql://' + POOLER + '/fixture?sslmode=prefer')
    monkeypatch.delenv('PGSSLROOTCERT', raising=False)

    secure.postgres_connect()
    secure.postgres_connect()
    factory.assert_called_once()
    options = factory.call_args.kwargs['kwargs']
    assert options['sslmode'] == 'verify-full'
    pem_file = Path(options['sslrootcert'])
    ssl.create_default_context(cafile=str(pem_file))
    certs = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',
                       pem_file.read_text(), re.DOTALL)
    assert len(certs) == 2
    assert {hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert)).hexdigest() for cert in certs} == {
        '807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa',
        '5f9b77951a7aa1303f9b58eea9bfa89e358cfdc15f9786ff10d4930a722c9ae2',
    }
