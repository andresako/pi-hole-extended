# Pi-hole Extended dashboard examples

[Español](#ejemplos-de-panel-para-pi-hole-extended)

[`examples/dashboards/pihole-extended.yaml`](../examples/dashboards/pihole-extended.yaml) is a three-view dashboard built from the activity card and Home Assistant's native cards, and [`examples/automations/unusual-activity-alert.yaml`](../examples/automations/unusual-activity-alert.yaml) notifies you when a device turns unusual.

## Use the dashboard example

1. Create a new dashboard in **Settings → Dashboards**, open it, choose **Edit dashboard → ⋮ → Raw configuration editor**, and paste the YAML. Do not paste it over an existing dashboard.
2. Replace the example entity IDs with yours. Open **Settings → Devices & services → Entities**, filter for `phe_`, and copy the IDs from the registry.

| Example ID | Entity |
|---|---|
| `sensor.phe_192_0_2_21_consultas_dns` | A device's **DNS queries** counter, used by the activity card |
| `sensor.phe_192_0_2_21_consultas_en_el_ultimo_minuto` | The same device's **Queries (last minute)** |
| `binary_sensor.phe_192_0_2_21_actividad_inusual` | The same device's **Unusual activity** |
| `sensor.pi_hole_192_0_2_2_diagnostico_…` | The four diagnostic entities of your Pi-hole entry |

`192.0.2.x` is a documentation address; your IDs contain each device's own address. Diagnostic IDs follow the language Home Assistant used when the entry was created.

The activity card needs no entity list: `type: custom:phe-activity-card` alone is a valid card, and you can pick the devices from the card editor.

## Automation example

Import the automation from **Settings → Automations & scenes → ⋮ → Edit in YAML**, replace the entity IDs, and change `notify.notify` to your notification service. It waits ten minutes in the *on* state before notifying, so brief bursts do not alert you. The binary sensor's attributes (`current_rate`, `usual_rate`, `threshold`, `times_usual`, `since`) can be used in the message.

## Live device card

`custom:phe-live-card` shows one device live: a gauge with the current queries per minute, the device's usual range and alert threshold, a ring with the allowed, blocked and provisional share, a 60-second strip of bars, and a status dot with the age of the last query. The gauge turns red when the device is unusual.

Add it from **Edit dashboard → Add card → Pi-hole Extended live device** and set everything in the card editor, or in YAML:

```yaml
type: custom:phe-live-card
entity: sensor.phe_192_0_2_21_consultas_dns
title: Kitchen speaker
```

`entity` is the device's **DNS queries** counter; the card finds the device's other entities and the diagnostics of its Pi-hole entry by itself. The editor can show or hide each part: `show_title`, `show_badge`, `show_gauge`, `show_ring`, `show_legend`, `show_strip` and `show_status` (all on by default). `animate` (off by default) smooths the movement of the gauge and the ring. Add one card per device, in a view of your own so you can arrange it. The card shows aggregate counts only, never domains.

Pi-hole is polled, so the strip spreads the queries that arrived between two updates over that time; it does not show the exact second of each query. The poll interval (15 seconds by default) sets how often the real values change.

## Several Pi-hole entries

Each Pi-hole entry has its own devices and diagnostics. The card lists the devices of every entry; use `entities` to restrict it, or add one card per entry.

---

# Ejemplos de panel para Pi-hole Extended

[`examples/dashboards/pihole-extended.yaml`](../examples/dashboards/pihole-extended.yaml) es un panel de tres vistas con la tarjeta de actividad y tarjetas nativas de Home Assistant, y [`examples/automations/unusual-activity-alert.yaml`](../examples/automations/unusual-activity-alert.yaml) te avisa cuando un dispositivo pasa a estado inusual.

## Usar el panel de ejemplo

1. Crea un panel nuevo en **Ajustes → Paneles**, ábrelo, elige **Editar panel → ⋮ → Editor de configuración en bruto** y pega el YAML. No lo pegues sobre un panel existente.
2. Sustituye los ID de ejemplo por los tuyos. En **Ajustes → Dispositivos y servicios → Entidades**, filtra por `phe_` y copia los ID del registro.

| ID de ejemplo | Entidad |
|---|---|
| `sensor.phe_192_0_2_21_consultas_dns` | El contador **Consultas DNS** de un dispositivo, usado por la tarjeta de actividad |
| `sensor.phe_192_0_2_21_consultas_en_el_ultimo_minuto` | **Consultas (último minuto)** del mismo dispositivo |
| `binary_sensor.phe_192_0_2_21_actividad_inusual` | **Actividad inusual** del mismo dispositivo |
| `sensor.pi_hole_192_0_2_2_diagnostico_…` | Las cuatro entidades de diagnóstico de tu entrada de Pi-hole |

`192.0.2.x` es una dirección de documentación; tus ID contienen la dirección de cada dispositivo. Los ID de diagnóstico siguen el idioma que usaba Home Assistant al crear la entrada.

La tarjeta de actividad no necesita lista de entidades: `type: custom:phe-activity-card` es una tarjeta válida y los dispositivos se eligen desde el editor de la tarjeta.

## Ejemplo de automatización

Importa la automatización desde **Ajustes → Automatizaciones y escenas → ⋮ → Editar en YAML**, sustituye los ID y cambia `notify.notify` por tu servicio de notificaciones. Espera diez minutos en estado *on* antes de avisar, así que las ráfagas breves no te molestan. Los atributos del sensor binario (`current_rate`, `usual_rate`, `threshold`, `times_usual`, `since`) sirven para el mensaje.

## Tarjeta en vivo de un dispositivo

`custom:phe-live-card` muestra un dispositivo en vivo: un indicador con las consultas por minuto actuales, el rango habitual y el umbral de aviso del dispositivo, un anillo con la proporción de permitidas, bloqueadas y provisionales, una cinta de barras de 60 segundos y un punto de estado con la antigüedad de la última consulta. El indicador se pone rojo cuando el dispositivo es inusual.

Añádela desde **Editar panel → Añadir tarjeta → Pi-hole Extended dispositivo en vivo** y ajusta todo en el editor de la tarjeta, o en YAML:

```yaml
type: custom:phe-live-card
entity: sensor.phe_192_0_2_21_consultas_dns
title: Altavoz de la cocina
```

`entity` es el contador **Consultas DNS** del dispositivo; la tarjeta encuentra sola las demás entidades del dispositivo y los diagnósticos de su entrada de Pi-hole. El editor permite mostrar u ocultar cada parte: `show_title`, `show_badge`, `show_gauge`, `show_ring`, `show_legend`, `show_strip` y `show_status` (todas activas por defecto). `animate` (desactivado por defecto) suaviza el movimiento del indicador y del anillo. Añade una tarjeta por dispositivo, en una vista tuya para poder ordenarlas. La tarjeta muestra solo recuentos agregados, nunca dominios.

Pi-hole se consulta por sondeo, así que la cinta reparte las consultas llegadas entre dos actualizaciones a lo largo de ese tiempo; no muestra el segundo exacto de cada una. El intervalo de sondeo (15 segundos por defecto) marca cada cuánto cambian los valores reales.

## Varias entradas de Pi-hole

Cada entrada de Pi-hole tiene sus dispositivos y diagnósticos. La tarjeta lista los dispositivos de todas las entradas; usa `entities` para limitarla o añade una tarjeta por entrada.
