"""API: administra hosts y lee métricas. Nginx (web) sirve la página y reenvía /api/ a este servicio.
Las consultas SNMP las hace el worker."""
import ipaddress
import os
from datetime import datetime, timezone

import pymysql
import docker
from requests.exceptions import RequestException
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024
POLL_INTERVAL = max(5, int(os.getenv('POLL_INTERVAL', '30')))


def connect_db():
    # "db" se resuelve por DNS dentro de la red que crea Docker Compose.
    return pymysql.connect(
        host=os.getenv('DB_HOST', 'db'), port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('DB_USER', 'monitor'), password=os.environ['DB_PASSWORD'],
        database=os.getenv('DB_NAME', 'monitor'), charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
        connect_timeout=5, read_timeout=10, write_timeout=10,
        init_command="SET time_zone = '+00:00'",
    )


def serialize(row):
    # Fechas UTC explícitas: el navegador las muestra en la hora local del usuario.
    return {key: value.replace(tzinfo=timezone.utc).isoformat()
            if isinstance(value, datetime) else value for key, value in row.items()}


@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify(error=error.description), error.code


@app.errorhandler(pymysql.IntegrityError)
def conflict(error):
    return jsonify(error='Ya existe un host con esa IP y puerto.'), 409


@app.errorhandler(pymysql.MySQLError)
def database_error(error):
    # No devolver SQL ni credenciales al navegador.
    app.logger.error('Error de base de datos (%s)', type(error).__name__)
    return jsonify(error='Base de datos no disponible. Revisá el servicio db.'), 503


@app.after_request
def response_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@app.get('/api/health')
def health():
    with connect_db() as db, db.cursor() as cursor:
        cursor.execute('SELECT 1')
    return jsonify(status='ok', poll_interval=POLL_INTERVAL)


@app.get('/api/app-status')
def app_status():
    """Estado informado por Docker; no depende de que MariaDB esté disponible."""
    project = os.getenv('DOCKER_PROJECT_NAME', '').strip()
    if not project:
        return jsonify(error='Falta DOCKER_PROJECT_NAME. Recreá api con Docker Compose.'), 503
    client = None
    try:
        # Equivale a consultar docker ps -a y docker inspect. No ejecuta comandos.
        client = docker.DockerClient(base_url='unix:///var/run/docker.sock',
                                     version='auto', timeout=3)
        containers = client.containers.list(all=True, filters={'label': [
            f'com.docker.compose.project={project}',
            'com.docker.compose.oneoff=False',
        ]}, ignore_removed=True)
        services = []
        for service in ('db', 'python', 'api', 'web'):
            matches = [container for container in containers
                       if container.labels.get('com.docker.compose.project') == project
                       and container.labels.get('com.docker.compose.service') == service
                       and container.labels.get('com.docker.compose.oneoff', '').lower() != 'true']
            row = dict(service=service, status='missing', health=None, name=None,
                       image=None, started_at=None, restart_count=None)
            if len(matches) == 1:
                container = matches[0]
                state = container.attrs.get('State', {})
                started = state.get('StartedAt', '')
                row.update(status=state.get('Status', 'unknown'), name=container.name,
                           health=state.get('Health', {}).get('Status'),
                           image=container.attrs.get('Config', {}).get('Image'),
                           started_at=started if started and not started.startswith('0001-') else None,
                           restart_count=container.attrs.get('RestartCount', 0))
            elif len(matches) > 1:
                # Esta práctica espera un contenedor por cada uno de sus servicios.
                row['status'] = 'multiple'
            services.append(row)
        # Sólo devolver estos campos; nunca exponer Env, mounts ni logs de inspect.
        return jsonify(services=services, checked_at=datetime.now(timezone.utc).isoformat())
    except (docker.errors.DockerException, RequestException, OSError) as error:
        app.logger.warning('No se pudo consultar Docker (%s)', type(error).__name__)
        return jsonify(error='No se pudo consultar Docker. Revisá que esté iniciado y que api tenga montado /var/run/docker.sock.'), 503
    finally:
        if client is not None:
            client.close()


@app.get('/api/hosts')
def list_hosts():
    with connect_db() as db, db.cursor() as cursor:
        # El índice latest_sample permite buscar la última muestra de cada host.
        cursor.execute('''
            SELECT h.id, h.name, h.ip, h.port, h.enabled,
                   s.checked_at, s.status, s.response_ms, s.sys_name, s.sys_descr,
                   s.uptime_seconds, s.cpu_percent, s.memory_percent,
                   s.memory_total_mb, s.error
            FROM hosts h LEFT JOIN samples s ON s.id = (
                SELECT id FROM samples WHERE host_id=h.id
                AND config_version=h.config_version ORDER BY id DESC LIMIT 1
            ) ORDER BY h.name, h.id
        ''')
        hosts = cursor.fetchall()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for host in hosts:
        host['enabled'] = bool(host['enabled'])
        if not host['enabled']:
            host['status'] = 'paused'
        elif not host['checked_at']:
            host['status'] = 'pending'
        elif (now - host['checked_at']).total_seconds() > max(90, POLL_INTERVAL * 3):
            host['status'] = 'stale'
    return jsonify(hosts=[serialize(host) for host in hosts], poll_interval=POLL_INTERVAL)


def validate_host(data, editing=False):
    if not isinstance(data, dict):
        raise ValueError('Se esperaba un objeto JSON.')
    name = data.get('name', '')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('El nombre debe tener entre 1 y 80 caracteres.')
    try:
        ip = ipaddress.IPv4Address(data.get('ip', ''))
        if not isinstance(data.get('ip'), str) or ip.is_multicast or ip.is_unspecified:
            raise ValueError()
    except (ValueError, ipaddress.AddressValueError):
        raise ValueError('Ingresá una dirección IPv4 válida, por ejemplo 192.168.1.20.')
    port = data.get('port', 161)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('El puerto debe ser un entero entre 1 y 65535.')
    community = data.get('community', '')
    if not isinstance(community, str) or len(community.encode('utf-8')) > 128:
        raise ValueError('La community debe tener como máximo 128 bytes.')
    if not community and not editing:
        raise ValueError('Ingresá la community SNMP.')
    enabled = data.get('enabled', True)
    if type(enabled) is not bool:
        raise ValueError('El estado habilitado debe ser verdadero o falso.')
    return name.strip(), str(ip), port, community, enabled


@app.route('/api/hosts', methods=['POST'])
@app.route('/api/hosts/<int:host_id>', methods=['PUT'])
def save_host(host_id=None):
    try:
        name, ip, port, community, enabled = validate_host(request.get_json(), host_id is not None)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    with connect_db() as db, db.cursor() as cursor:
        if host_id is None:
            cursor.execute('INSERT INTO hosts (name, ip, port, community, enabled) VALUES (%s,%s,%s,%s,%s)',
                           (name, ip, port, community, enabled))
            host_id = cursor.lastrowid
            code = 201
        else:
            # Community vacía conserva el secreto existente; nunca se envía en GET.
            cursor.execute('''UPDATE hosts SET name=%s, ip=%s, port=%s,
                community=IF(%s='', community, %s), enabled=%s,
                config_version=config_version+1 WHERE id=%s''',
                (name, ip, port, community, community, enabled, host_id))
            if not cursor.rowcount:
                return jsonify(error='Host no encontrado.'), 404
            code = 200
    return jsonify(id=host_id), code


@app.delete('/api/hosts/<int:host_id>')
def delete_host(host_id):
    with connect_db() as db, db.cursor() as cursor:
        cursor.execute('DELETE FROM hosts WHERE id=%s', (host_id,))
        if not cursor.rowcount:
            return jsonify(error='Host no encontrado.'), 404
    # ON DELETE CASCADE borra también las muestras del host.
    return '', 204


@app.get('/api/hosts/<int:host_id>/history')
def history(host_id):
    with connect_db() as db, db.cursor() as cursor:
        cursor.execute('SELECT config_version FROM hosts WHERE id=%s', (host_id,))
        host = cursor.fetchone()
        if host is None:
            return jsonify(error='Host no encontrado.'), 404
        cursor.execute('''SELECT checked_at, status, response_ms, cpu_percent, memory_percent
            FROM samples WHERE host_id=%s AND config_version=%s ORDER BY id DESC LIMIT 120''',
            (host_id, host['config_version']))
        samples = list(reversed(cursor.fetchall()))
    return jsonify(samples=[serialize(sample) for sample in samples])


if __name__ == '__main__':
    # Servidor de Flask, sin modo debug.
    app.run(host='0.0.0.0', port=5000, debug=False)
