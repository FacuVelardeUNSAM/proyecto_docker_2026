"""Pruebas de API. La integración requiere una BD descartable terminada en _test."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pymysql
import api


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(name='Ubuntu web', ip='192.168.1.20', port=161,
                         community='lab-read-only', enabled=True)
        self.client = api.app.test_client()

    def test_rejects_invalid_fields(self):
        invalid = [('ip', 'not-an-ip'), ('ip', '::1'), ('ip', 123),
                   ('ip', '0.0.0.0'), ('port', 0), ('port', 65536),
                   ('port', True), ('port', 161.5), ('name', ' '),
                   ('community', ''), ('community', 'é' * 65), ('enabled', 'false')]
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    api.validate_host({**self.data, field: value})

    def test_empty_community_allowed_only_when_editing(self):
        self.assertEqual(api.validate_host({**self.data, 'community': ''}, editing=True)[3], '')

    def test_requires_json_object(self):
        for data in (None, [], 'text', 12):
            with self.assertRaises(ValueError):
                api.validate_host(data)
        self.assertEqual(self.client.post('/api/hosts', data='name=test').status_code, 415)

    def test_database_failure_is_actionable_and_private(self):
        with patch.object(api, 'connect_db', side_effect=pymysql.OperationalError(2003, 'secret-test')):
            response = self.client.get('/api/hosts')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('secret-test', response.get_data(as_text=True))


@unittest.skipUnless(os.getenv('RUN_DB_TESTS') == '1', 'Requiere RUN_DB_TESTS=1 y BD descartable')
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.getenv('DB_NAME', '').endswith('_test'):
            raise RuntimeError('Usar solamente una BD de pruebas terminada en _test.')
        with api.connect_db() as db, db.cursor() as cursor:
            schema = Path(__file__).parents[1] / 'DB' / 'init.sql'
            for sql in schema.read_text().split(';'):
                if sql.strip():
                    cursor.execute(sql)

    def setUp(self):
        self.client = api.app.test_client()
        with api.connect_db() as db, db.cursor() as cursor:
            cursor.execute('DELETE FROM hosts')
        self.data = dict(name='Ubuntu web', ip='127.0.0.1', port=1161,
                         community='lab-read-only', enabled=True)

    def create(self):
        response = self.client.post('/api/hosts', json=self.data)
        self.assertEqual(response.status_code, 201)
        return response.json['id']

    def test_crud_duplicate_and_secrets(self):
        host_id = self.create()
        self.assertEqual(self.client.post('/api/hosts', json=self.data).status_code, 409)
        host = self.client.get('/api/hosts').json['hosts'][0]
        self.assertEqual(host['status'], 'pending')
        self.assertNotIn('community', host)
        self.assertNotIn('lab-read-only', str(host))
        response = self.client.put(f'/api/hosts/{host_id}', json={**self.data, 'name': "Ubuntu ' test", 'community': ''})
        self.assertEqual(response.status_code, 200)
        with api.connect_db() as db, db.cursor() as cursor:
            cursor.execute('SELECT * FROM hosts WHERE id=%s', (host_id,))
            saved = cursor.fetchone()
        self.assertEqual(saved['community'], 'lab-read-only')
        self.assertEqual(saved['name'], "Ubuntu ' test")
        self.assertEqual(self.client.delete(f'/api/hosts/{host_id}').status_code, 204)
        self.assertEqual(self.client.get('/api/hosts').json['hosts'], [])
        self.assertEqual(self.client.get(f'/api/hosts/{host_id}/history').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/hosts/{host_id}').status_code, 404)
        self.assertEqual(self.client.put(f'/api/hosts/{host_id}', json=self.data).status_code, 404)

    def test_stale_paused_history_limit_and_cascade(self):
        host_id = self.create()
        with api.connect_db() as db, db.cursor() as cursor:
            cursor.executemany("INSERT INTO samples(host_id,config_version,status,checked_at) VALUES(%s,1,'up',UTC_TIMESTAMP()-INTERVAL 10 MINUTE)", [(host_id,)] * 130)
        self.assertEqual(self.client.get('/api/hosts').json['hosts'][0]['status'], 'stale')
        history = self.client.get(f'/api/hosts/{host_id}/history').json['samples']
        self.assertEqual(len(history), 120)
        self.assertTrue(history[0]['checked_at'].endswith('+00:00'))
        self.client.put(f'/api/hosts/{host_id}', json={**self.data, 'enabled': False})
        self.assertEqual(self.client.get('/api/hosts').json['hosts'][0]['status'], 'paused')
        self.client.delete(f'/api/hosts/{host_id}')
        with api.connect_db() as db, db.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) AS n FROM samples WHERE host_id=%s', (host_id,))
            self.assertEqual(cursor.fetchone()['n'], 0)

    def test_injection_is_stored_as_text(self):
        self.data['name'] = "<script>alert(1)</script>'; DROP TABLE hosts;--"
        self.create()
        hosts = self.client.get('/api/hosts').json['hosts']
        self.assertEqual(len(hosts), 1)
        self.assertEqual(hosts[0]['name'], self.data['name'])


if __name__ == '__main__':
    unittest.main()
