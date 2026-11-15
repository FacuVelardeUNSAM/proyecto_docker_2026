"""Pruebas contra un agente SNMP de laboratorio, nunca contra servidores de producción."""
import asyncio
import os
import unittest

import worker


@unittest.skipUnless(os.getenv('TEST_SNMP_PORT'), 'Requiere un agente SNMP de laboratorio')
class SnmpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = worker.SnmpEngine()
        self.host = dict(ip='127.0.0.1', port=int(os.environ['TEST_SNMP_PORT']),
                         community='lab-read-only')

    async def asyncTearDown(self):
        self.engine.close_dispatcher()

    async def test_real_snmp_metrics(self):
        sample = await worker.poll_host(self.engine, self.host)
        self.assertEqual(sample['status'], 'up')
        self.assertIsNotNone(sample['uptime_seconds'])
        self.assertIn('Linux', sample['sys_descr'])
        self.assertGreaterEqual(sample['cpu_percent'], 0)
        self.assertLessEqual(sample['cpu_percent'], 100)
        self.assertGreater(sample['memory_total_mb'], 0)
        self.assertGreaterEqual(sample['memory_percent'], 0)
        self.assertLessEqual(sample['memory_percent'], 100)

    async def test_wrong_community_reports_down(self):
        sample = await worker.poll_host(self.engine, {**self.host, 'community': 'incorrect-community'})
        self.assertEqual(sample['status'], 'down')
        self.assertIsNone(sample['cpu_percent'])
        self.assertIsNone(sample['response_ms'])
        self.assertNotIn('incorrect-community', sample['error'])

    async def test_missing_optional_metrics_stays_up(self):
        sample = await worker.poll_host(self.engine, {**self.host, 'community': 'lab-system-only'})
        self.assertEqual(sample['status'], 'up')
        self.assertIsNone(sample['cpu_percent'])
        self.assertIsNone(sample['memory_percent'])
        self.assertIsNone(sample['memory_total_mb'])


@unittest.skipUnless(os.getenv('RUN_DB_TESTS') == '1', 'Requiere BD de pruebas descartable')
class StorageTests(unittest.TestCase):
    def setUp(self):
        if not os.getenv('DB_NAME', '').endswith('_test'):
            raise RuntimeError('Usar solamente una BD terminada en _test.')
        with worker.connect_db() as db, db.cursor() as cursor:
            cursor.execute('DELETE FROM hosts')
            cursor.execute("INSERT INTO hosts(name,ip,port,community) VALUES('test','127.0.0.1',1161,'lab-read-only')")
        self.host = worker.read_hosts()[0]
        self.sample = dict(status='up', response_ms=5, sys_name='test', sys_descr='Linux test',
                           uptime_seconds=100, cpu_percent=12, memory_percent=30,
                           memory_total_mb=1024, error=None)

    def sample_count(self):
        with worker.connect_db() as db, db.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) AS n FROM samples')
            return cursor.fetchone()['n']

    def test_result_of_old_configuration_is_discarded(self):
        with worker.connect_db() as db, db.cursor() as cursor:
            cursor.execute('UPDATE hosts SET config_version=config_version+1 WHERE id=%s', (self.host['id'],))
        worker.save_sample(self.host, self.sample)
        self.assertEqual(self.sample_count(), 0)

    def test_deleted_or_paused_host_is_not_reinserted(self):
        with worker.connect_db() as db, db.cursor() as cursor:
            cursor.execute('UPDATE hosts SET enabled=FALSE WHERE id=%s', (self.host['id'],))
        worker.save_sample(self.host, self.sample)
        self.assertEqual(self.sample_count(), 0)
        with worker.connect_db() as db, db.cursor() as cursor:
            cursor.execute('DELETE FROM hosts WHERE id=%s', (self.host['id'],))
        worker.save_sample(self.host, self.sample)
        self.assertEqual(self.sample_count(), 0)

    def test_retention_only_deletes_old_samples(self):
        worker.save_sample(self.host, self.sample)
        with worker.connect_db() as db, db.cursor() as cursor:
            cursor.execute("INSERT INTO samples(host_id,config_version,status,checked_at) VALUES(%s,1,'up',UTC_TIMESTAMP()-INTERVAL 30 DAY)", (self.host['id'],))
        worker.cleanup()
        self.assertEqual(self.sample_count(), 1)


if __name__ == '__main__':
    unittest.main()
