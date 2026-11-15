# Validación del proyecto

## Comprobaciones realizadas

- 13 pruebas de API, validación de entradas, persistencia y SNMP contra MariaDB y Net-SNMP reales.
- Flujo del proceso `worker.py`: lectura de hosts → consultas SNMP v2c → almacenamiento de muestras.
- Respuesta correcta, community incorrecta y ausencia de OID opcionales.
- Alta, edición, duplicados, pausa, eliminación e historial limitado a 120 muestras.
- Descarte de resultados anteriores a una edición o eliminación del host.
- Retención de muestras y borrado del historial por cascada.
- Dashboard en Chromium: tarjetas, búsqueda, gráficos ECharts y vista ampliada.
- Formularios nativos, conservación de community al editar, confirmación de eliminación y cierre con Escape.
- Diseño en escritorio (1440 px) y móvil (390 px), sin desbordamiento horizontal.
- Preferencia de movimiento reducido y aviso ante un fallo de la API.
- Nombres con HTML tratados como texto; communities excluidas de las respuestas de lectura.
- Ninguna solicitud externa de la interfaz: ECharts se sirve desde el mismo contenedor.
- Sintaxis de Python/JavaScript y validación con `docker compose config --quiet`.

## Entorno utilizado

Python 3.12, MariaDB 11.8.6, Net-SNMP 5.9.4 y Chromium Headless 156 en Linux. Las pruebas usaron una base descartable y un agente local; no se consultaron servidores del usuario. La aplicación inicia sin hosts ni datos de demostración.

En este agente, CPU comenzó a estar disponible cerca de un minuto después de arrancar `snmpd`. Durante ese intervalo se mostró N/D, conservando el estado de respuesta SNMP.

## Límite de esta validación

El entorno de trabajo no dispone de un daemon Docker. **No se ejecutó `docker compose up --build` ni se construyeron las imágenes aquí.** Se comprobó la configuración de Compose y se ejecutaron directamente sus componentes.

El Compose utiliza MariaDB **11.4**, por lo que resta comprobar el arranque de esas imágenes, el healthcheck, la persistencia del bind mount y la conectividad hacia el Ubuntu real en la VM de la presentación. Los pasos están en el README.

## Incorporación de la vista Estado de la app

- 9 pruebas nuevas para la API: filtros de proyecto/servicio, contenedores detenidos, healthchecks, contenedores faltantes, duplicados, ausencia de socket/configuración, timeouts y ocultación de credenciales/logs.
- Se verificó que `/api/app-status` no consulta MariaDB y sólo permite GET.
- Se probó el SDK Docker 7.1.0 contra un servidor HTTP de prueba que reproduce `/version`, `/containers/json` y `/containers/{id}/json`; se comprobaron la negociación de versión y los filtros enviados.
- Pruebas de navegador: estados running/exited/restarting/unhealthy, pérdida de conexión sin conservar estados verdes, navegación entre las tres vistas, funcionamiento con la API de hosts caída y diseño móvil.
- Compose conserva exactamente tres servicios; `DOCKER_PROJECT_NAME` coincide con el nombre generado y con un nombre indicado mediante `-p`.

Las respuestas Docker de estas pruebas son controladas. **No se dispone de un daemon Docker real en este entorno**, por lo que queda pendiente verificar el montaje del socket y comparar la pestaña con `docker compose ps -a` en la VM del usuario.

Las capturas y los archivos de pruebas de Web se retiraron posteriormente para simplificar la entrega del TP. Este documento registra las verificaciones realizadas antes de esa limpieza.
