# Pi-hole Extended para Home Assistant

Integración personalizada e independiente de la integración oficial de Pi-hole. Consulta la API REST de Pi-hole v6, registra en Actividad cada consulta nueva detectada y crea un dispositivo por cliente.

## Qué registra

Cada entrada incluye la hora de la consulta, nombre e IP del cliente (si Pi-hole los conoce), dominio, decisión permitida/bloqueada y estado original de Pi-hole. Añade tipo DNS, tipo de respuesta, CNAME bloqueado, servidor ascendente y texto EDE cuando la respuesta de la API incluye esos campos. Las categorías y clientes configurados en Pi-hole se conservan tal como los presente la API; la integración no cambia su configuración.

Cada cliente aparece como un dispositivo con seis entidades: los contadores acumulados **Consultas DNS**, **Permitidas** y **Bloqueadas**, y las tasas **Consultas por segundo**, **Permitidas por segundo** y **Bloqueadas por segundo**. Los contadores reflejan las consultas observadas desde que se añadió la integración. Las tasas se calculan entre sondeos correctos; arrancan en cero después del inicio y se actualizan en el siguiente sondeo. Si no hay consultas nuevas, muestran cero. La entidad **Consultas DNS** conserva además nombre/IP, la última consulta y hasta diez consultas recientes en sus atributos. Las consultas individuales aparecen en **Ajustes → Sistema → Registros → Actividad** (Logbook). Si Actividad usa un filtro de dominios incluidos, añade `pihole_queries`.

Los IDs de sus sensores empiezan por `sensor.phe_`; por ejemplo, `sensor.phe_192_168_68_94_consultas_dns`. Ese prefijo permite localizarlos y filtrarlos en Recorder mediante `entity_globs`. Si ya usas un bloque `recorder.include`, puedes añadir:

```yaml
recorder:
  include:
    entity_globs:
      - sensor.phe_*
```

Ten en cuenta que `recorder.include` hace que Recorder guarde solo las entidades incluidas. La versión 0.5.1 migra los IDs generados por defecto y conserva los IDs que ya se hubieran cambiado manualmente. Si una automatización o un panel usaba el ID anterior, actualiza esa referencia después de la migración.

## Requisito de persistencia

La integración llama a la acción pública `logbook.log` para crear cada entrada personalizada y las etiqueta con el dominio `pihole_queries`. El mensaje contiene la fecha/hora original reportada por Pi-hole; la hora de la fila en Logbook/Recorder es la hora en que Home Assistant la registra, normalmente hasta un intervalo de sondeo después. Recorder guarda el evento y su historial alimenta Logbook/Actividad. La retención sigue la configuración de Recorder (por defecto, purga automática; normalmente 10 días). Un filtro de Actividad con dominios incluidos debe incluir `pihole_queries`; sin filtros de inclusión, normalmente se muestran automáticamente. Si Recorder excluye el evento `logbook_entry`, no habrá historial persistente.

No se duplica toda la consulta DNS en almacenamiento propio: Home Assistant Recorder es el mecanismo de historial. La integración solo guarda el último identificador leído en el almacenamiento local de la entrada para reanudar el sondeo tras reiniciar Home Assistant.

## Instalación manual

1. Copia la carpeta `custom_components/pihole_queries` a `custom_components/pihole_queries` dentro del directorio de configuración de Home Assistant.
2. Reinicia Home Assistant.
3. Ve a **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Pi-hole Extended** y completa el formulario.

El repositorio está en [andresako/pi-hole-extended](https://github.com/andresako/pi-hole-extended). Puede añadirse como repositorio personalizado desde **HACS → Integraciones → ⋮ → Repositorios personalizados**, categoría **Integración**. La inclusión en el catálogo predeterminado de HACS queda pausada hasta verificar la integración; la solicitud anterior está cerrada. Para instalación manual, copia la carpeta indicada arriba y reinicia Home Assistant. Después de actualizar los archivos, reinicia Home Assistant.

## Configuración

- **Host**: nombre o IP de Pi-hole, sin ruta. Por ejemplo, `pi.hole` o `192.168.1.2`.
- **Puerto**: puerto donde escucha la interfaz/API web de Pi-hole (normalmente 80 o 443).
- **HTTPS**: actívalo si Pi-hole sirve la API por HTTPS.
- **Verificar el certificado HTTPS**: déjalo activado si el certificado es de confianza. Desactívalo solo si Pi-hole usa un certificado autofirmado; la conexión seguirá cifrada, pero Home Assistant no validará la identidad del servidor.
- **Contraseña de aplicación**: crea una en la interfaz de Pi-hole y úsala aquí. No uses ni compartas una contraseña dentro de este proyecto. Se guarda en el campo privado de datos de la entrada de configuración de Home Assistant y no se escribe en los registros de la integración.
- **Frecuencia**: entre 1 y 3600 segundos; el valor inicial sigue siendo 15. Se puede cambiar luego en **Opciones** de la integración.

Pi-hole v6 autentica con `POST /api/auth` y una sesión temporal SID. La integración la solicita de nuevo tras un `401`, sin imprimir contraseñas ni SID en los logs. Usa el puerto/protocolo adecuado y, si se usa TLS, un certificado que Home Assistant confíe. Consulta la [documentación oficial de autenticación de Pi-hole](https://docs.pi-hole.net/api/auth/) y la [documentación de la API](https://docs.pi-hole.net/api/).

A 1 segundo, en condiciones normales habrá unas 60 lecturas autenticadas de `GET /api/queries` por minuto; se reutiliza un único SID y no se inicia sesión en cada sondeo. Ponerse al día después de una acumulación puede requerir llamadas paginadas adicionales. Pi-hole documenta la limitación de intentos de inicio de sesión de `POST /api/auth`, pero no publica un límite numérico para las lecturas autenticadas normales. Por eso, 1 segundo es una opción de alta frecuencia, no un máximo garantizado como seguro. Si Pi-hole es un dispositivo de baja potencia, empieza con 5 segundos y vigila su carga. Por separado, FTL anterior a 6.7 tiene un problema upstream del servidor web/API ante ráfagas descontroladas; actualiza FTL a 6.7 o posterior antes de exponer la API a redes no confiables.

**Si Pi-hole muestra “API seats exceeded”**: detén los reintentos de configuración y deshabilita o descarga temporalmente esta integración. Al cerrarse correctamente, envía `DELETE /api/auth` para liberar su SID; las sesiones huérfanas caducan según el tiempo de sesión configurado en Pi-hole. La versión 0.3.2 garantiza también el cierre del SID cuando falla la carga inicial. No aumentes `webserver.api.max_sessions` como arreglo: eso solo eleva el límite y puede ocultar sesiones que no se están cerrando. Otras aplicaciones también pueden ocupar sesiones.

Durante la configuración, la integración consulta `GET /api/queries` para comprobar credenciales y acceso. Pi-hole sirve su especificación exacta en `http(s)://<host>:<puerto>/api/docs`; esa documentación local es la referencia definitiva para la versión instalada. No se proporcionó acceso a una instancia de Pi-hole en este espacio de desarrollo, así que no fue posible abrir la documentación privada/local de tu instalación. La implementación sigue el [esquema oficial de `GET /api/queries` de Pi-hole FTL](https://github.com/pi-hole/FTL/blob/master/src/api/docs/content/specs/queries.yaml): respuesta `queries`, ID, timestamp, estado, cliente, tipo, respuesta y `cursor` para paginación. Si el esquema que muestra tu instancia difiere, la validación de conexión informará del endpoint no compatible en lugar de asumir otra estructura.

Home Assistant documenta las [entradas personalizadas de Activity/Logbook](https://www.home-assistant.io/integrations/logbook/#custom-entries) y la [persistencia de eventos en Recorder](https://www.home-assistant.io/integrations/recorder). Esos mecanismos son los que usa este diseño.

## Intervalo, retrasos y límites

Las entradas suelen aparecer dentro de un intervalo de sondeo más el tiempo de respuesta de red. Por ejemplo, con 15 segundos de intervalo, una consulta puede tardar aproximadamente hasta 15 segundos en aparecer. Pi-hole alimenta la API desde su historial de consultas en memoria, así que no se espera el retardo de escritura periódica de su base de datos en disco.

Cada llamada pide 1000 filas; si hay una acumulación, se recorre por páginas usando el `cursor` oficial y el ID de consulta hasta alcanzar el cursor previamente guardado. Pi-hole limita cada respuesta a un máximo de 10 000 registros; la paginación evita depender de un único lote. Una acumulación extraordinariamente grande puede alargar un ciclo y aumentar memoria/llamadas a la API. Si el historial en memoria de Pi-hole se vacía antes de que Home Assistant vuelva a sondear, las consultas que Pi-hole ya no exponga no se pueden recuperar desde esta integración. Tampoco se importan consultas anteriores a la primera instalación. El cursor se basa en IDs de la base de Pi-hole: si esa base se recrea y los IDs retroceden, se establece un cursor nuevo y no se intenta reconciliar historiales incompatibles. Los contadores comienzan en cero tras crear la integración; la instantánea inicial encuentra clientes y muestra su última consulta sin contarla. Los dispositivos de clientes retirados se conservan para mantener su historial de Recorder.

El registro por consulta aumenta el volumen de Recorder y de Logbook, especialmente en redes con muchas peticiones DNS. Usa un intervalo razonable y revisa la retención de Recorder y el almacenamiento disponible. El intervalo mínimo configurable es 1 s. A ese ritmo se generan hasta 86 400 solicitudes API al día por instancia; si no necesitas una actualización casi instantánea, usa 5 s o más para reducir las lecturas y la carga de Recorder.

Si Home Assistant se detiene exactamente entre emitir una entrada y guardar el cursor, podría repetirse esa última entrada después del reinicio; Home Assistant no ofrece una transacción atómica entre evento de Logbook y almacenamiento del cursor de una integración.

## Actualizar y desinstalar

**Actualizar:** sustituye la carpeta de integración por la versión nueva y reinicia Home Assistant. Los datos de cursor se mantienen mientras conserves la entrada.

**Desinstalar:** elimina la integración desde **Ajustes → Dispositivos y servicios**, borra `custom_components/pihole_queries` y reinicia Home Assistant. El historial ya escrito en Recorder permanece sujeto a su política normal de purga.

## Estructura

```text
custom_components/pihole_queries/
  __init__.py       # ciclo de vida de la entrada
  api.py            # autenticación SID y GET /api/queries
  config_flow.py    # configuración y opciones desde la interfaz
  const.py
  coordinator.py    # sondeo, cursor, resumen por cliente y eventos Activity
  sensor.py         # dispositivo y entidades sencillas por cliente DNS
  manifest.json
  strings.json
```

La versión 0.5.1 antepone `phe` a los IDs de sensores para facilitar los filtros de Recorder. La versión 0.4.1 permite configurar un sondeo de 1 segundo; la 0.4.0 añadió las tasas de consultas por segundo por cliente y eliminó las entidades de último dominio y último resultado.
