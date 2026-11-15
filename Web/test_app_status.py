"""Estado Docker con respuestas controladas: no necesita un daemon en los tests."""
import os
import unittest
from unittest.mock import patch

import docker
from docker.models.containers import Container
from requests.exceptions import ReadTimeout

import api


def container(service, status='running', health=None, project='nodo-test', oneoff='False'):
    state = {'Status': status, 'StartedAt': '2026-11-15T10:00:00.123456789Z'}
    if health:
        state['Health'] = {'Status': health, 'Log': [{'Output': 'secret-log'}]}
    return Container(attrs={
        'Id': f'{service}-test-id', 'Name': f'/{project}-{service}-1',
        'State': state, 'RestartCount': 2,
        'Config': {'Image': f'nodo/{service}:test', 'Env': ['DB_PASSWORD=secret-env'],
                   'Labels': {'com.docker.compose.project': project,
                              'com.docker.compose.service': service,
                              'com.docker.compose.oneoff': oneoff}},
        'Mounts': [{'Source': '/secret-host-path'}],
    })


class AppStatusTests(unittest.TestCase):
    def setUp(self):
        self.client = api.app.test_client()
        self.env = patch.dict(os.environ, DOCKER_PROJECT_NAME='nodo-test')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.docker_patch = patch.object(api.docker, 'DockerClient')
        self.factory = self.docker_patch.start()
        self.addCleanup(self.docker_patch.stop)
        self.engine = self.factory.return_value
        self.engine.containers.list.return_value = [container('db', health='healthy'),
                                                   container('python'), container('web')]

    def test_native_fields_without_database_or_secrets(self):
        with patch.object(api, 'connect_db', side_effect=AssertionError('No consultar SQL')):
            response = self.client.get('/api/app-status')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['service'] for row in response.json['services']], ['db', 'python', 'web'])
        db = response.json['services'][0]
        self.assertEqual(db['status'], 'running')
        self.assertEqual(db['health'], 'healthy')
        self.assertEqual(db['restart_count'], 2)
        self.assertEqual(db['name'], 'nodo-test-db-1')
        self.assertIsNone(response.json['services'][1]['health'])
        self.assertNotIn('secret', response.get_data(as_text=True))
        self.assertTrue(response.json['checked_at'].endswith('+00:00'))
        self.engine.containers.list.assert_called_once_with(all=True, filters={'label': [
            'com.docker.compose.project=nodo-test', 'com.docker.compose.oneoff=False']}, ignore_removed=True)
        self.factory.assert_called_once_with(base_url='unix:///var/run/docker.sock', version='auto', timeout=3)
        self.engine.close.assert_called_once()

    def test_stopped_and_unhealthy_are_distinct(self):
        self.engine.containers.list.return_value = [container('db', 'exited', 'healthy'),
            container('python', 'restarting'), container('web', 'running', 'unhealthy')]
        rows = self.client.get('/api/app-status').json['services']
        self.assertEqual([(r['status'], r['health']) for r in rows],
                         [('exited', 'healthy'), ('restarting', None), ('running', 'unhealthy')])

    def test_missing_foreign_oneoff_and_other_services(self):
        self.engine.containers.list.return_value = [container('db', project='another-project'),
            container('python', oneoff='True'), container('unrelated'), container('web')]
        rows = self.client.get('/api/app-status').json['services']
        self.assertEqual([r['status'] for r in rows], ['missing', 'missing', 'running'])

    def test_multiple_containers_not_reported_as_one_healthy_service(self):
        self.engine.containers.list.return_value += [container('db')]
        self.assertEqual(self.client.get('/api/app-status').json['services'][0]['status'], 'multiple')

    def test_container_never_started(self):
        created = container('python', 'created')
        created.attrs['State']['StartedAt'] = '0001-01-01T00:00:00Z'
        self.engine.containers.list.return_value = [created]
        row = self.client.get('/api/app-status').json['services'][1]
        self.assertEqual(row['status'], 'created')
        self.assertIsNone(row['started_at'])

    def test_missing_project_never_queries_all_containers(self):
        with patch.dict(os.environ, DOCKER_PROJECT_NAME=''):
            self.assertEqual(self.client.get('/api/app-status').status_code, 503)
        self.factory.assert_not_called()

    def test_unavailable_docker_returns_unknown_not_stopped(self):
        for error in (docker.errors.DockerException('secret-error'), PermissionError('secret-error'), ReadTimeout('secret-error')):
            with self.subTest(error=type(error).__name__):
                self.factory.side_effect = error
                response = self.client.get('/api/app-status')
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('secret', response.get_data(as_text=True))
                self.assertNotIn('services', response.json)

    def test_connection_closed_on_list_error(self):
        self.engine.containers.list.side_effect = docker.errors.APIError('secret-error')
        self.assertEqual(self.client.get('/api/app-status').status_code, 503)
        self.engine.close.assert_called_once()

    def test_endpoint_has_no_control_operations(self):
        self.assertEqual(self.client.post('/api/app-status').status_code, 405)
        self.assertEqual(self.client.delete('/api/app-status').status_code, 405)
        self.factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
