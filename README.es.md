# Pi-hole DNS Queries para Home Assistant

Integración personalizada e independiente de la integración oficial de Pi-hole. Consulta la API REST de Pi-hole v6 y crea una entrada del Logbook/Actividad por cada consulta nueva detectada.

## Qué registra

Cada entrada incluye la hora de la consulta, nombre e IP del cliente (si Pi-hole los conoce), dominio, decisión permitida/bloqueada y estado original de Pi-hole. Añade tipo DNS, tipo de respuesta, CNAME bloqueado, servidor ascendente y texto EDE cuando la respuesta de la API incluye esos campos. Las categorías y clientes configurados en Pi-hole se conservan tal como los presente la API; la integración no cambia su configuración.

Las consultas aparecen en **Ajustes → Sistema → Registros → Actividad** (Logbook). No se crean sensores por consulta: la actividad es el registro individual soportado por Home Assistant y evita que una entidad cambie miles de veces.

## Requisito de persistencia

La integración llama a la acción pública `logbook.log` para crear cada entrada personalizada. El mensaje contiene la fecha/hora original reportada por Pi-hole; la hora de la fila en Logbook/Recorder es la hora en que Home Assistant la registra, normalmente hasta un intervalo de sondeo después. Recorder guarda el evento y su historial alimenta Logbook/Actividad. La retención sigue la configuración de Recorder (por defecto, purga automática; normalmente 10 días). Si Recorder excluye el evento `logbook_entry` o el dominio `logbook`, no habrá historial persistente. El filtro de Logbook también puede ocultar entradas.

No se duplica toda la consulta DNS en almacenamiento propio: Home Assistant Recorder es el mecanismo de historial. La integración solo guarda el último identificador leído en el almacenamiento local de la entrada para reanudar el sondeo tras reiniciar Home Assistant.

## Instalación manual

1. Copia la carpeta `custom_components/pihole_queries` a `custom_components/pihole_queries` dentro del directorio de configuración de Home Assistant.
2. Reinicia Home Assistant.
3. Ve a **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Pi-hole DNS Queries** y completa el formulario.

El repositorio público es [andresako/pi-hole-extended](https://github.com/andresako/pi-hole-extended). Para instalarlo ahora desde HACS, abre **HACS → Integraciones → ⋮ → Repositorios personalizados**, añade `andresako/pi-hole-extended`, elige la categoría **Integración**, instálalo y reinicia Home Assistant. La solicitud para incluirlo en el catálogo predeterminado de HACS se revisa por separado y todavía no está aprobada. Para instalación manual, copia la carpeta indicada arriba y reinicia Home Assistant. Después de actualizar los archivos, reinicia Home Assistant.

## Configuración

- **Host**: nombre o IP de Pi-hole, sin ruta. Por ejemplo, `pi.hole` o `192.168.1.2`.
- **Puerto**: puerto donde escucha la interfaz/API web de Pi-hole (normalmente 80 o 443).
- **HTTPS**: actívalo si Pi-hole sirve la API por HTTPS.
- **Contraseña de aplicación**: crea una en la interfaz de Pi-hole y úsala aquí. No uses ni compartas una contraseña dentro de este proyecto. Se guarda en el campo privado de datos de la entrada de configuración de Home Assistant y no se escribe en los registros de la integración.
- **Frecuencia**: entre 5 y 3600 segundos; el valor inicial es 15. Se puede cambiar luego en **Opciones** de la integración.

Pi-hole v6 autentica con `POST /api/auth` y una sesión temporal SID. La integración la solicita de nuevo tras un `401`, sin imprimir contraseñas ni SID en los logs. Usa el puerto/protocolo adecuado y, si se usa TLS, un certificado que Home Assistant confíe. Consulta la [documentación oficial de autenticación de Pi-hole](https://docs.pi-hole.net/api/auth/) y la [documentación de la API](https://docs.pi-hole.net/api/).

Durante la configuración, la integración consulta `GET /api/queries` para comprobar credenciales y acceso. Pi-hole sirve su especificación exacta en `http(s)://<host>:<puerto>/api/docs`; esa documentación local es la referencia definitiva para la versión instalada. No se proporcionó acceso a una instancia de Pi-hole en este espacio de desarrollo, así que no fue posible abrir la documentación privada/local de tu instalación. La implementación sigue el [esquema oficial de `GET /api/queries` de Pi-hole FTL](https://github.com/pi-hole/FTL/blob/master/src/api/docs/content/specs/queries.yaml): respuesta `queries`, ID, timestamp, estado, cliente, tipo, respuesta y `cursor` para paginación. Si el esquema que muestra tu instancia difiere, la validación de conexión informará del endpoint no compatible en lugar de asumir otra estructura.

Home Assistant documenta las [entradas personalizadas de Activity/Logbook](https://www.home-assistant.io/integrations/logbook/#custom-entries) y la [persistencia de eventos en Recorder](https://www.home-assistant.io/integrations/recorder). Esos mecanismos son los que usa este diseño.

## Intervalo, retrasos y límites

Las entradas suelen aparecer dentro de un intervalo de sondeo más el tiempo de respuesta de red. Por ejemplo, con 15 segundos de intervalo, una consulta puede tardar aproximadamente hasta 15 segundos en aparecer. Pi-hole alimenta la API desde su historial de consultas en memoria, así que no se espera el retardo de escritura periódica de su base de datos en disco.

Cada llamada pide 1000 filas; si hay una acumulación, se recorre por páginas usando el `cursor` oficial y el ID de consulta hasta alcanzar el cursor previamente guardado. Pi-hole limita cada respuesta a un máximo de 10 000 registros; la paginación evita depender de un único lote. Una acumulación extraordinariamente grande puede alargar un ciclo y aumentar memoria/llamadas a la API. Si el historial en memoria de Pi-hole se vacía antes de que Home Assistant vuelva a sondear, las consultas que Pi-hole ya no exponga no se pueden recuperar desde esta integración. Tampoco se importan consultas anteriores a la primera instalación. El cursor se basa en IDs de la base de Pi-hole: si esa base se recrea y los IDs retroceden, se establece un cursor nuevo y no se intenta reconciliar historiales incompatibles.

El registro por consulta aumenta el volumen de Recorder y de Logbook, especialmente en redes con muchas peticiones DNS. Usa un intervalo razonable y revisa la retención de Recorder y el almacenamiento disponible. El intervalo mínimo es 5 s; intervalos más breves no son aceptados.

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
  coordinator.py    # sondeo, cursor y eventos Logbook
  manifest.json
  strings.json
```
