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

## One view per device

A view with `strategy: type: custom:phe-device` shows one device: its unusual-activity verdict (with the current and usual rate when it is unusual), the activity chart, allowed, blocked and provisional counts, and the coverage and freshness of the Pi-hole entry's data. It is generated from the device's own entities, so there are no IDs to copy.

```yaml
views:
  - title: Kitchen speaker
    path: kitchen-speaker
    strategy:
      type: custom:phe-device
      entity: sensor.phe_192_0_2_21_consultas_dns
```

Use `entity` (the device's **DNS queries** counter) or `device` (a device ID). `name` overrides the title. Add one view per device. The view shows aggregate counts only, never domains.

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

## Una vista por dispositivo

Una vista con `strategy: type: custom:phe-device` muestra un solo dispositivo: su veredicto de actividad inusual (con el ritmo actual y el habitual cuando lo es), la gráfica de actividad, las consultas permitidas, bloqueadas y provisionales, y la cobertura y la frescura de los datos de la entrada de Pi-hole. Se genera a partir de las entidades del propio dispositivo, así que no hay ID que copiar.

```yaml
views:
  - title: Altavoz de la cocina
    path: altavoz-cocina
    strategy:
      type: custom:phe-device
      entity: sensor.phe_192_0_2_21_consultas_dns
```

Usa `entity` (el contador **Consultas DNS** del dispositivo) o `device` (un ID de dispositivo). `name` cambia el título. Añade una vista por dispositivo. La vista muestra solo recuentos agregados, nunca dominios.

## Varias entradas de Pi-hole

Cada entrada de Pi-hole tiene sus dispositivos y diagnósticos. La tarjeta lista los dispositivos de todas las entradas; usa `entities` para limitarla o añade una tarjeta por entrada.
