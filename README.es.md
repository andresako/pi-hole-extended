# Pi-hole Extended

**Consulta en Home Assistant la actividad DNS que Pi-hole v6 detecta, organizada por cliente.** Pi-hole Extended ofrece contadores por cliente y diagnósticos de conexión y cobertura de consultas.

> Integración personalizada creada por la comunidad. No está afiliada ni cuenta con el respaldo de Home Assistant o Pi-hole.

[![Última versión](https://img.shields.io/github/v/release/Andresako/pi-hole-extended?label=versión)](https://github.com/Andresako/pi-hole-extended/releases/latest) [![Validación HACS](https://github.com/andresako/pi-hole-extended/actions/workflows/hacs.yaml/badge.svg)](https://github.com/andresako/pi-hole-extended/actions/workflows/hacs.yaml) [![Hassfest](https://github.com/andresako/pi-hole-extended/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/andresako/pi-hole-extended/actions/workflows/hassfest.yaml)

## De un vistazo

- Un dispositivo de Home Assistant por cada cliente que aparece en el historial de consultas de Pi-hole.
- Cinco sensores por cliente: consultas observadas acumuladas, consultas en la ventana del último minuto de Pi-hole, consultas permitidas, bloqueadas y provisionales.
- Cuatro diagnósticos de la integración: estado de comunicación, última lectura correcta, última consulta observada y cobertura de ingesta.
- Elige si Home Assistant Activity registra todas las consultas nuevas, solo las bloqueadas o ninguna. La integración no conserva detalles de consultas en atributos de sensores ni en su Store.
- Un panel inicial con cuatro vistas nativas de Home Assistant: **Resumen**, **Clientes**, **Detalle** y **Diagnóstico**.

La integración cuenta las consultas nuevas que observa al consultar Pi-hole. No reconstruye todo el tráfico que Pi-hole haya gestionado. El total de entidades es 29 para 5 clientes, 129 para 25 clientes y 504 para 100 clientes (cinco por cliente y cuatro diagnósticos de la integración).

## Vista previa del panel

Las capturas muestran un panel de ejemplo de Home Assistant con cuatro vistas personalizadas. El gráfico de 24 horas usa observaciones sintéticas importadas en un Recorder desechable para la demostración; Pi-hole Extended empieza a contar tras configurarse y no recupera consultas históricas de Pi-hole.

![Resumen de Pi-hole Extended con totales sintéticos por cliente y un gráfico DNS de 24 horas](docs/images/overview.png)

*Datos de demostración: observaciones simuladas de Home Assistant. Los totales por cliente y el historial del gráfico son sintéticos y muestran la disposición del panel.*

![Diagnóstico de Pi-hole Extended con historial DNS sintético y entidades de estado de la integración](docs/images/diagnostics.png)

*Datos de demostración: observaciones simuladas de Home Assistant. El gráfico es un historial Recorder de ejemplo; el estado de conexión e ingesta corresponde a la instancia de demostración aislada.*

## Requisitos

- Home Assistant **2025.1 o posterior**.
- Pi-hole **v6**. El contrato de la API de consultas se contrasta con FTL **6.1.0–6.8.x**. Pi-hole también ofrece su referencia de API en `/api/docs`.

## Instalar con HACS

Pi-hole Extended se instala añadiendo su repositorio como repositorio personalizado de HACS.

1. En Home Assistant, abre **HACS → Integraciones → ⋮ → Repositorios personalizados**.
2. Añade `https://github.com/Andresako/pi-hole-extended` y selecciona **Integración**.
3. Instala **Pi-hole Extended** y reinicia Home Assistant.
4. Abre **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Pi-hole Extended** y sigue el formulario.

Para instalarla manualmente, descarga [`pihole_queries-0.9.0.zip`](https://github.com/Andresako/pi-hole-extended/releases/download/0.9.0/pihole_queries-0.9.0.zip), extrae `custom_components/pihole_queries` en el directorio de configuración de Home Assistant y reinicia Home Assistant.

## Configurar Pi-hole

Crea una contraseña de aplicación en **Settings** de la interfaz web de Pi-hole. Pi-hole solo muestra la contraseña una vez; consulta la [guía de autenticación de Pi-hole](https://docs.pi-hole.net/api/auth/).

Durante la configuración, indica:

- El nombre de host o la dirección IP de Pi-hole, sin protocolo, puerto ni ruta.
- El puerto web/API.
- Si se debe usar HTTPS y verificar el certificado TLS.
- La contraseña de aplicación.
- El intervalo de sondeo, de **1 a 3600 segundos** (**15 segundos** de forma predeterminada).

La contraseña se guarda en la entrada de integración de Home Assistant y no se incluye en los registros de la integración. Un intervalo menor puede mostrar antes las consultas nuevas y genera más solicitudes a la API.

## Ver las primeras observaciones

Después de configurar la integración, espera a que se complete un sondeo y revisa **Estado de comunicación** y **Cobertura de ingesta** en las entidades de diagnóstico. La primera lectura establece una línea base: no cuenta las consultas que ya estaban en la instantánea actual de Pi-hole. Después, genera una consulta DNS desde un dispositivo que use este Pi-hole y espera al siguiente sondeo.

El contador **Consultas DNS** del cliente aumenta cuando se observa una consulta nueva. Las consultas permitidas y bloqueadas incrementan sus contadores del clasificador; los resultados desconocidos o incompletos quedan como provisionales y no entran en el porcentaje clasificado. Con Activity habilitada, una consulta nueva también puede aparecer en **Activity** de Home Assistant si la opción lo permite. Home Assistant controla la persistencia de Activity y Recorder.

## Elegir qué conservar

En las opciones de la integración, elige un modo de conservación:

- **Activity habilitada** (predeterminado): envía eventos nuevos a Activity según la opción seleccionada. La integración no conserva detalles de consultas.
- **Solo totales**: mantiene identidades de clientes y totales numéricos y no crea nuevas entradas de Activity.

Consulta los [detalles técnicos y límites](docs/DETALLES_ES.md) sobre conservación y volumen elevado.

## Añadir el panel de ejemplo

El [YAML del panel inicial](examples/dashboards/pihole-extended.yaml) incluye cuatro vistas creadas con tarjetas nativas de Home Assistant. Usa IDs de entidades de ejemplo: copia el YAML en un panel nuevo y asigna cada marcador a los IDs de tu propio registro de entidades. Sigue la [guía del panel](docs/dashboard-examples.md); no sustituyas la configuración de un panel existente.

## Detectar un dispositivo con comportamiento anómalo con la tarjeta de actividad

La integración incluye una tarjeta de Lovelace, que se carga sola, con la actividad DNS por dispositivo y los picos inusuales resaltados. Añádela en cualquier panel con **Editar panel → Añadir tarjeta → Manual**:

```yaml
type: custom:phe-activity-card
```

Todo se configura desde el editor de la tarjeta (**Editar tarjeta**): uno o varios dispositivos (hasta 12), el tamaño de bloque (de 5 min a 1 día), el rango (de 6 horas a 30 días), líneas o barras, escala logarítmica y si se avisa de dispositivos con actividad inusual (contador rojo y lista, también para dispositivos que no estén en la gráfica; no se muestra nada mientras todos estén normales). Los ajustes se guardan en el panel, así que todos ven la misma gráfica. Sin dispositivos elegidos, la tarjeta muestra los tres más activos. Claves YAML: `title`, `entities`, `bucket`, `span`, `chart_type` (`line` o `bar`), `log_scale`, `show_overview` y `names` (`{entity_id: etiqueta}`).

Un dispositivo se marca como **inusual** cuando se mantiene muy por encima de su propio patrón reciente: varias veces su nivel habitual, por encima de su pico diario típico y de una tasa mínima, durante al menos 15 minutos. Los dispositivos tranquilos o con ráfagas regulares no se marcan por su comportamiento normal. La tarjeta usa las estadísticas a largo plazo del contador **Consultas DNS**, así que el eje de tiempo es el momento en que Home Assistant recibió el dato, los bloques de 5/15/30 minutos solo existen para rangos de hasta 10 días (valor por defecto del Recorder) y un dispositivo nuevo necesita aproximadamente un día de historial para tener referencia. Solo se leen recuentos agregados; nunca dominios.

## Solución de problemas

- **Los contadores no aumentan:** revisa el estado de comunicación, host, puerto, ajustes TLS, contraseña, intervalo de sondeo y si Pi-hole aún expone la consulta en su historial en memoria.
- **Aumentan los contadores, pero Activity está vacío:** confirma que esté seleccionada la opción Activity habilitada, que Activity permita los eventos que quieres registrar y que sus filtros de Home Assistant permitan el evento. La integración no verifica la persistencia.

## Enlaces

- [Guía del panel](docs/dashboard-examples.md) · [Detalles técnicos y limitaciones](docs/DETALLES_ES.md)
- [Informar de un problema](https://github.com/Andresako/pi-hole-extended/issues) · [Política de seguridad](SECURITY.md) · [Licencia MIT](LICENSE)
- [Read this guide in English](README.md)
