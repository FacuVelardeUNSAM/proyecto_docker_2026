"""Bucle de monitoreo: MariaDB → SNMP v2c → MariaDB."""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import pymysql
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData, ContextData, ObjectIdentity, ObjectType, SnmpEngine,
    UdpTransportTarget, bulk_walk_cmd, get_cmd,
)
from pysnmp.proto.rfc1905 import EndOfMibView, NoSuchInstance, NoSuchObject

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
LOG = logging.getLogger('snmp-worker')
POLL_INTERVAL = max(5, int(os.getenv('POLL_INTERVAL', '30')))
RETENTION_DAYS = max(1, int(os.getenv('RETENTION_DAYS', '7')))
MISSING = (NoSuchObject, NoSuchInstance, EndOfMibView)
SYSTEM_OIDS = ['1.3.6.1.2.1.1.1.0', '1.3.6.1.2.1.1.3.0', '1.3.6.1.2.1.1.5.0']
CPU_OID = '1.3.6.1.2.1.25.3.3.1.2'
MEMORY_OIDS = [f'1.3.6.1.4.1.2021.4.{number}.0' for number in (5, 6, 14, 15)]


def connect_db():
    return pymysql.connect(
        host=os.getenv('DB_HOST', 'db'), port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('DB_USER', 'monitor'), password=os.environ['DB_PASSWORD'],
        database=os.getenv('DB_NAME', 'monitor'), charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
        connect_timeout=5, read_timeout=10, write_timeout=10,
        init_command="SET time_zone = '+00:00'",
    )


async def snmp_get(engine, auth, target, oids):
    indication, status, _, bindings = await get_cmd(
        engine, auth, target, ContextData(),
        *[ObjectType(ObjectIdentity(oid)) for oid in oids], lookupMib=False,
    )
    if indication:
        raise RuntimeError('Sin respuesta SNMP: revisar conectividad, puerto, community y ACL.')
    if status:
        raise RuntimeError('El agente SNMP rechazó la consulta.')
    return [None if isinstance(value, MISSING) else value for _, value in bindings]


async def cpu_load(engine, auth, target):
    loads = []
    # GETBULK recorre la columna de CPU; se limita el recorrido a 256 procesadores.
    async for indication, status, _, bindings in bulk_walk_cmd(
        engine, auth, target, ContextData(), 0, 16,
        ObjectType(ObjectIdentity(CPU_OID)),
        lexicographicMode=False, lookupMib=False, maxCalls=16,
    ):
        if indication or status:
            return None
        for oid, value in bindings:
            if not str(oid).startswith(CPU_OID + '.') or isinstance(value, MISSING):
                continue
            load = int(value)
            if 0 <= load <= 100:
                loads.append(load)
    return round(sum(loads) / len(loads), 1) if loads else None


async def poll_host(engine, host):
    sample = dict(status='down', response_ms=None, sys_name=None, sys_descr=None,
                  uptime_seconds=None, cpu_percent=None, memory_percent=None,
                  memory_total_mb=None, error=None)
    # mpModel=1 significa SNMP v2c. Timeout 2 s, con un reintento.
    auth = CommunityData(host['community'], mpModel=1)
    target = await UdpTransportTarget.create((host['ip'], host['port']), timeout=2, retries=1)
    try:
        started = time.monotonic()
        description, uptime, name = await snmp_get(engine, auth, target, SYSTEM_OIDS)
        response_ms = round((time.monotonic() - started) * 1000, 1)
        if all(value is None for value in (description, uptime, name)):
            raise RuntimeError('El agente no expone los OID de sistema. Revisar la vista SNMP.')
        sample.update(status='up', response_ms=response_ms,
                      sys_name=str(name)[:255] if name is not None else None,
                      sys_descr=str(description)[:4096] if description is not None else None,
                      # sysUpTime se expresa en centésimas de segundo.
                      uptime_seconds=int(uptime) // 100 if uptime is not None else None)
    except (RuntimeError, ValueError, TypeError) as error:
        sample['error'] = str(error)[:255]
        return sample

    # Los OID opcionales faltantes no convierten un host que responde en "caído".
    try:
        sample['cpu_percent'] = await asyncio.wait_for(cpu_load(engine, auth, target), timeout=8)
    except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError):
        pass
    try:
        values = await snmp_get(engine, auth, target, MEMORY_OIDS)
        if values[0] is not None and int(values[0]) > 0:
            sample['memory_total_mb'] = round(int(values[0]) / 1024, 1)
        if all(value is not None for value in values) and int(values[0]) > 0:
            total, free, buffers, cached = map(int, values)
            # Uso estimado de RAM excluyendo buffers y caché de Net-SNMP.
            sample['memory_percent'] = round(max(0, min(100, (total-free-buffers-cached)*100/total)), 1)
    except (RuntimeError, ValueError, TypeError):
        pass
    return sample


def save_sample(host, sample):
    with connect_db() as db, db.cursor() as cursor:
        db.begin()
        try:
            # Bloqueo breve: no reinsertar un host eliminado ni una configuración vieja.
            cursor.execute('SELECT config_version, enabled FROM hosts WHERE id=%s FOR UPDATE', (host['id'],))
            current = cursor.fetchone()
            if not current or not current['enabled'] or current['config_version'] != host['config_version']:
                db.rollback()
                return
            cursor.execute('''INSERT INTO samples
                (host_id, config_version, checked_at, status, response_ms, sys_name,
                 sys_descr, uptime_seconds, cpu_percent, memory_percent, memory_total_mb, error)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (host['id'], host['config_version'], datetime.now(timezone.utc).replace(tzinfo=None),
                 sample['status'], sample['response_ms'], sample['sys_name'], sample['sys_descr'],
                 sample['uptime_seconds'], sample['cpu_percent'], sample['memory_percent'],
                 sample['memory_total_mb'], sample['error']))
            db.commit()
        except Exception:
            db.rollback()
            raise


def read_hosts():
    with connect_db() as db, db.cursor() as cursor:
        cursor.execute('SELECT * FROM hosts WHERE enabled=TRUE ORDER BY id')
        return cursor.fetchall()


def cleanup():
    with connect_db() as db, db.cursor() as cursor:
        cursor.execute('DELETE FROM samples WHERE checked_at < UTC_TIMESTAMP() - INTERVAL %s DAY',
                       (RETENTION_DAYS,))


async def main():
    engine = SnmpEngine()
    next_cleanup = 0
    LOG.info('Worker iniciado: pausa entre rondas %ss; retención %s días.', POLL_INTERVAL, RETENTION_DAYS)
    try:
        while True:
            try:
                hosts = read_hosts()
                if time.monotonic() >= next_cleanup:
                    cleanup()
                    next_cleanup = time.monotonic() + 3600
                for host in hosts:
                    try:
                        sample = await poll_host(engine, host)
                        save_sample(host, sample)
                        LOG.info('Host %s (%s:%s): %s', host['id'], host['ip'], host['port'], sample['status'])
                    except Exception as error:
                        # Un equipo con un error inesperado no detiene los demás.
                        LOG.error('No se pudo consultar/guardar host %s (%s)', host['id'], type(error).__name__)
            except pymysql.MySQLError as error:
                LOG.warning('BD no disponible (%s); se reintentará.', type(error).__name__)
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        engine.close_dispatcher()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOG.info('Worker detenido.')
