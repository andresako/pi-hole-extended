# Pi-hole Extended dashboard examples

The starter dashboard in [`examples/dashboards/pihole-extended.yaml`](../examples/dashboards/pihole-extended.yaml) provides four native Home Assistant views: Summary, Clients, Details, and Diagnostics. It contains replaceable entity IDs, not a working dashboard until you map them to entities in your Home Assistant instance.

## Install a personal copy

1. In Home Assistant, open **Settings → Devices & services → Entities**. Filter for Pi-hole Extended and identify the entries for the client and source you want to show. Open each entity to copy its current entity ID from the entity registry. Use the registry value even if it differs from an example or from the sensor's original name; entity IDs can be customized.
2. Create a new dashboard from **Settings → Dashboards**. Give it a name such as “Pi-hole”; leave your existing dashboards in place.
3. Open the new dashboard's menu, choose **Edit dashboard**, then **Raw configuration editor**. Paste the example YAML into this new dashboard and replace every `sensor.replace_with_…` ID with a real entity ID. Save the dashboard.
4. Remove rows you do not use, or duplicate rows for additional clients. The starter graphs show at most three client series; Home Assistant's native graph was verified with five. For more clients, add series manually and check the result on your screen size.

If your Home Assistant dashboard editor does not offer a raw YAML editor for a newly created dashboard, follow its UI's YAML dashboard setup and create a separate dashboard file. Do not replace the configuration for an existing dashboard. The example is a copy: later changes to this repository's file do not alter the dashboard you already saved in Home Assistant.

## Map the example entities

Replace the placeholders by selecting entities in the registry; never build IDs from client IPs, names, `phe_`, or any other prefix.

| Placeholder suffix | Entity to choose |
|---|---|
| `client_1_queries` | Client's **Consultas DNS** sensor |
| `client_2_queries` | Client's **Consultas DNS** sensor |
| `client_3_queries` | Client's **Consultas DNS** sensor |
| `client_4_queries` | Client's **Consultas DNS** sensor |
| `client_1_queries_last_minute` | Client's **Queries (last minute)** sensor |
| `client_2_queries_last_minute` | Client's **Queries (last minute)** sensor |
| `client_3_queries_last_minute` | Client's **Queries (last minute)** sensor |
| `client_1_allowed_v1` | Client's **Allowed queries** sensor |
| `client_2_allowed_v1` | Client's **Allowed queries** sensor |
| `client_1_blocked_v1` | Client's **Blocked queries** sensor |
| `client_2_blocked_v1` | Client's **Blocked queries** sensor |
| `client_3_blocked_v1` | Client's **Blocked queries** sensor |
| `client_1_provisional_v1` | Client's **Provisional queries** sensor |
| `client_2_provisional_v1` | Client's **Provisional queries** sensor |
| `unknown_client_queries` | **Consultas DNS** for the unknown client, when present |
| `entry_communication_status` | Per-entry diagnostic **Estado de comunicación** |
| `entry_last_successful_read` | Per-entry diagnostic **Última lectura correcta** |
| `entry_last_query_observed` | Per-entry diagnostic **Última consulta observada** |
| `entry_ingestion_coverage` | Per-entry diagnostic **Cobertura de ingesta** |

The Summary activity card uses the dedicated **Queries (last minute)** sensors. They count by Pi-hole's `query.time` where it is valid, matching the original rolling-window meaning; missing or invalid times fall back to when Home Assistant observed the query. The integration uses anonymous one-second numeric buckets for rolling rates, so counts can differ by up to one second at the window boundary. The sensor is unavailable when the API sample is stale or coverage is unknown/partial, and after startup until more than 60 seconds of complete observation are available. The blocked-count card compares a small, manually selected set of cumulative classifier counters. It is an observed lifetime count for each selected entity, not a time-period ranking. The history graph shows changes in cumulative totals recorded by Home Assistant over its displayed time range; it does not reconstruct Pi-hole's original query time. Counters from different classifier epochs or source generations must not be compared as one series. Provisional observations are not included in the classified percentage. To inspect `active_classifier_version` and `blocked_percentage`, open the client's **Consultas DNS** entity in Details and expand **Attributes** in Home Assistant's entity dialog. A real 0% remains 0; if there are no allowed or blocked observations, the percentage is unknown rather than 0%.

The four tabs provide standard navigation. In Details, selecting an entity opens Home Assistant's standard entity dialog with the history and attributes that are available. This dashboard does not provide a complete DNS query table, interactive search, coordinated filters, per-user data access, or a privacy boundary. Hiding a tab does not restrict access to its data.

## Multiple Pi-hole entries

Each entry has its own diagnostic entities and client entities. Choose all rows in a view from one entry/source generation. For a second Pi-hole, duplicate the dashboard or its views and map every row to that entry in the entity registry. Do not assume that equal client names or IPs imply shared entities.

## Tested scope and limits

This native-card example is based on a dashboard tested on Home Assistant Core 2025.1.0. It uses the built-in `entities`, `history-graph`, and `markdown` cards. The YAML was imported with manual entity mapping and observed at 390×844 and 1440×900 CSS pixels, with all four views and keyboard navigation. A separate prototype was checked with long names, zero, unavailable, and diagnostic states. Those observations do not establish behavior on other Home Assistant releases or screen sizes.

The rolling last-minute sensor is not a historical per-period query volume or a reconstructed Recorder series. Recorder history remains subject to Home Assistant retention and exclusions; the integration does not verify event persistence.

---

# Ejemplos de panel para Pi-hole Extended

El panel inicial de [`examples/dashboards/pihole-extended.yaml`](../examples/dashboards/pihole-extended.yaml) ofrece cuatro vistas nativas de Home Assistant: Summary, Clients, Details y Diagnostics. Contiene IDs de entidades reemplazables; el panel no estará operativo hasta asignarlos a entidades de tu Home Assistant.

## Instalar una copia personal

1. En Home Assistant, abre **Ajustes → Dispositivos y servicios → Entidades**. Filtra por Pi-hole Extended e identifica los sensores del cliente y origen que quieras mostrar. Abre cada entidad y copia su ID actual del registro. Usa el valor del registro aunque no coincida con el ejemplo o con el nombre original del sensor: los IDs pueden personalizarse.
2. Crea un panel nuevo desde **Ajustes → Paneles**. Ponle un nombre, por ejemplo, «Pi-hole»; conserva tus paneles existentes.
3. Abre el menú del panel nuevo, elige **Editar panel** y después **Editor de configuración sin procesar**. Pega el YAML de ejemplo en ese panel nuevo y reemplaza todos los IDs `sensor.replace_with_…` por IDs reales. Guarda el panel.
4. Elimina las filas que no uses o duplica filas para más clientes. Los gráficos iniciales muestran hasta tres series; el gráfico nativo se probó con cinco. Si añades más, comprueba el resultado en tu tamaño de pantalla.

Si el editor de paneles de tu Home Assistant no ofrece un editor YAML para un panel nuevo, sigue el procedimiento YAML de esa interfaz y crea un archivo de panel independiente. No reemplaces la configuración de un panel existente. El ejemplo se copia: cambios posteriores en el archivo de este repositorio no modifican el panel que ya guardaste en Home Assistant.

## Asignar las entidades del ejemplo

Reemplaza los marcadores eligiendo entidades del registro; nunca construyas IDs a partir de IPs, nombres de cliente, `phe_` u otro prefijo.

| Sufijo del marcador | Entidad que debes elegir |
|---|---|
| `client_1_queries` | Sensor **Consultas DNS** del cliente |
| `client_2_queries` | Sensor **Consultas DNS** del cliente |
| `client_3_queries` | Sensor **Consultas DNS** del cliente |
| `client_4_queries` | Sensor **Consultas DNS** del cliente |
| `client_1_queries_last_minute` | Sensor **Consultas (último minuto)** del cliente |
| `client_2_queries_last_minute` | Sensor **Consultas (último minuto)** del cliente |
| `client_3_queries_last_minute` | Sensor **Consultas (último minuto)** del cliente |
| `client_1_allowed_v1` | Sensor **Consultas permitidas** del cliente |
| `client_2_allowed_v1` | Sensor **Consultas permitidas** del cliente |
| `client_1_blocked_v1` | Sensor **Consultas bloqueadas** del cliente |
| `client_2_blocked_v1` | Sensor **Consultas bloqueadas** del cliente |
| `client_3_blocked_v1` | Sensor **Consultas bloqueadas** del cliente |
| `client_1_provisional_v1` | Sensor **Consultas provisionales** del cliente |
| `client_2_provisional_v1` | Sensor **Consultas provisionales** del cliente |
| `unknown_client_queries` | **Consultas DNS** del cliente desconocido, si existe |
| `entry_communication_status` | Diagnóstico por entrada **Estado de comunicación** |
| `entry_last_successful_read` | Diagnóstico por entrada **Última lectura correcta** |
| `entry_last_query_observed` | Diagnóstico por entrada **Última consulta observada** |
| `entry_ingestion_coverage` | Diagnóstico por entrada **Cobertura de ingesta** |

La tarjeta de actividad en Summary usa los sensores independientes **Consultas (último minuto)**. Cuentan por la hora `query.time` de Pi-hole cuando es válida, recuperando el significado original de la ventana; si falta o no es válida, usan la hora en que HA observó la consulta. En modo agregado se guardan cubos numéricos por segundo, por lo que el conteo puede variar hasta un segundo en el borde de la ventana. El sensor no está disponible si la muestra API está obsoleta, la cobertura es desconocida/parcial, o hasta superar 60 segundos de observación completa después del arranque. La tarjeta de bloqueadas compara un conjunto pequeño elegido manualmente de contadores acumulados del clasificador. Es un total observado desde la instalación por entidad, no un ranking por periodo. El gráfico muestra cambios en los acumulados registrados por Home Assistant durante el intervalo visible; no reconstruye la hora original de cada consulta en Pi-hole. No combines en una misma serie contadores de distintas épocas del clasificador o generaciones de origen. Las observaciones provisionales no se incluyen en el porcentaje clasificado. Para consultar `active_classifier_version` y `blocked_percentage`, abre **Consultas DNS** del cliente en Details y despliega **Atributos** en el cuadro de entidad de Home Assistant. Un 0 % real sigue siendo 0; si aún no hay observaciones permitidas ni bloqueadas, el porcentaje queda desconocido y no se convierte en 0 %.

Las cuatro pestañas usan la navegación estándar. En Details, al seleccionar una entidad se abre el cuadro estándar de Home Assistant con el historial y atributos disponibles. Este panel no proporciona una tabla DNS completa, búsqueda interactiva, filtros coordinados, control de acceso por usuario ni separación privada de datos. Ocultar una pestaña no restringe el acceso a sus datos.

## Varias entradas de Pi-hole

Cada entrada tiene sus propias entidades de diagnóstico y de cliente. Elige todas las filas de una vista dentro de la misma entrada/generación. Para otro Pi-hole, duplica el panel o sus vistas y asigna cada fila a esa entrada desde el registro de entidades. No supongas que clientes con el mismo nombre o IP comparten entidades.

## Alcance y límites probados

Este ejemplo de tarjetas nativas se basa en un panel probado con Home Assistant Core 2025.1.0. Usa las tarjetas integradas `entities`, `history-graph` y `markdown`. El YAML se importó con asignación manual de entidades y se observó a 390×844 y 1440×900 píxeles CSS, con las cuatro vistas y navegación por teclado. Por separado, un prototipo se comprobó con nombres largos y estados cero, no disponible y diagnóstico. Esto no demuestra el comportamiento en otras versiones de Home Assistant ni otros tamaños de pantalla.

El sensor móvil del último minuto no es un volumen histórico por periodos ni una serie de Recorder reconstruida. El historial de Recorder depende de la retención y exclusiones de Home Assistant; la integración no comprueba la persistencia de los eventos.
