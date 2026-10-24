# Pi-hole Extended

**Consulta en Home Assistant la actividad DNS que Pi-hole v6 detecta, organizada por cliente.** Pi-hole Extended añade contadores claros por cliente, detalles recientes opcionales y diagnósticos para comprobar si Home Assistant mantiene el ritmo.

> Integración personalizada creada por la comunidad. No está afiliada ni cuenta con el respaldo de Home Assistant o Pi-hole.

[![Última versión](https://img.shields.io/github/v/release/Andresako/pi-hole-extended?label=versión)](https://github.com/Andresako/pi-hole-extended/releases/latest) [![Validación HACS](https://github.com/andresako/pi-hole-extended/actions/workflows/hacs.yaml/badge.svg)](https://github.com/andresako/pi-hole-extended/actions/workflows/hacs.yaml) [![Hassfest](https://github.com/andresako/pi-hole-extended/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/andresako/pi-hole-extended/actions/workflows/hassfest.yaml)

## De un vistazo

- Un dispositivo de Home Assistant por cada cliente que aparezca en el historial de consultas visible de Pi-hole.
- Cinco sensores por cliente: consultas DNS acumuladas, consultas del último minuto, permitidas, bloqueadas y provisionales. La versión del clasificador se conserva internamente para que los cambios posteriores sigan siendo claros.
- Elige **Historial detallado de consultas** para conservar detalles recientes y, de forma opcional, entradas en Activity; o **Solo totales agregados** para conservar identidades de clientes y totales numéricos sin filas ni dominios de consultas.
- Diagnósticos por entrada para el estado de comunicación, la última lectura correcta, la última consulta observada y la cobertura de ingesta.
- Un panel inicial con cuatro vistas nativas de Home Assistant: **Resumen**, **Clientes**, **Detalle** y **Diagnóstico**.

La integración cuenta las consultas nuevas que observa al consultar Pi-hole. No reconstruye todo el tráfico que Pi-hole haya gestionado ni ordena consultas por su hora original.

## Vista previa del panel

Las capturas muestran un panel de ejemplo de Home Assistant con cuatro vistas personalizadas. El gráfico de 24 horas usa observaciones sintéticas importadas en un Recorder desechable para la demostración; Pi-hole Extended empieza a contar tras configurarse y no recupera consultas históricas de Pi-hole.

![Resumen de Pi-hole Extended con totales sintéticos por cliente y un gráfico DNS de 24 horas](docs/images/overview.png)

*Datos de demostración: observaciones simuladas de Home Assistant. Los totales por cliente y el historial del gráfico son sintéticos y muestran la disposición del panel.*

![Diagnóstico de Pi-hole Extended con historial DNS sintético y entidades de estado de la integración](docs/images/diagnostics.png)

*Datos de demostración: observaciones simuladas de Home Assistant. El gráfico es un historial Recorder de ejemplo; el estado de conexión e ingesta corresponde a la instancia de demostración aislada.*

## Requisitos

- Home Assistant **2025.1 o posterior**.
- Pi-hole **v6**. El contrato de la API de consultas se contrasta con FTL **6.1.0–6.8.x**; Pi-hole también sirve su propia referencia de API en `/api/docs`.

## Instalar con HACS

Pi-hole Extended se instala como repositorio personalizado de HACS y actualmente no está en el catálogo predeterminado.

1. En Home Assistant, abre **HACS → Integraciones → ⋮ → Repositorios personalizados**.
2. Añade `https://github.com/Andresako/pi-hole-extended` y selecciona **Integración**.
3. Instala **Pi-hole Extended** y reinicia Home Assistant.
4. Abre **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Pi-hole Extended** y sigue el formulario.

Para instalarla manualmente, descarga [`pihole_queries-0.8.0.zip`](https://github.com/Andresako/pi-hole-extended/releases/download/0.8.0/pihole_queries-0.8.0.zip), extrae `custom_components/pihole_queries` en el directorio de configuración de Home Assistant y reinicia.

## Configurar Pi-hole

Crea una contraseña de aplicación en **Settings** de la interfaz web de Pi-hole. Pi-hole solo muestra la contraseña una vez; consulta la [guía de autenticación de Pi-hole](https://docs.pi-hole.net/api/auth/).

Durante la configuración, indica:

- El nombre de host o la dirección IP de Pi-hole, sin protocolo, puerto ni ruta.
- El puerto web/API. Se usa exactamente como se indica, también con HTTPS.
- Si se debe usar HTTPS y verificar el certificado TLS.
- La contraseña de aplicación.
- El intervalo de sondeo en segundos (15 de forma predeterminada).

La contraseña se guarda en la entrada de integración de Home Assistant y no se incluye en los registros de la integración. Empieza con el intervalo predeterminado; un intervalo menor puede mostrar antes las consultas nuevas y genera más solicitudes a la API.

## Ver las primeras observaciones

Después de configurar la integración, espera a que se complete un sondeo y revisa **Estado de comunicación** y **Cobertura de ingesta** en las entidades de diagnóstico. La primera lectura establece una línea base: no cuenta las consultas que ya estaban en la instantánea actual de Pi-hole. Después, genera una consulta DNS desde un dispositivo que use este Pi-hole y espera al siguiente sondeo.

El contador **Consultas DNS** del cliente debería aumentar cuando se observe la consulta nueva. Las consultas permitidas y bloqueadas incrementan sus contadores del clasificador; los resultados desconocidos o incompletos quedan como provisionales y no entran en el porcentaje clasificado. En modo Detallado, la consulta nueva también puede aparecer en **Activity** de Home Assistant si la opción de Activity lo permite. El texto actual de las entradas de Activity está en español. Home Assistant controla la persistencia de Activity y Recorder.

## Elegir qué conservar

En las opciones de la integración, elige uno de estos modos:

- **Historial detallado de consultas** (predeterminado): conserva detalles recientes para los sensores de cliente. Activity puede registrar **Todos los eventos**, **Solo eventos bloqueados** o **Sin eventos de Activity**.
- **Solo totales agregados**: conserva identidades de clientes y totales numéricos, sin dominios, IDs, estados brutos ni filas de consultas individuales. Desactiva las nuevas entradas de Activity.

Al cambiar al modo Agregado se eliminan los detalles de consultas del Store actual de la integración y de su copia heredada. No se borra lo que Home Assistant ya guardó, el historial de Activity, exportaciones ni copias de seguridad. Consulta los [detalles técnicos y límites](docs/DETALLES_ES.md) sobre retención y volumen elevado.

## Añadir el panel de ejemplo

El [YAML del panel inicial](examples/dashboards/pihole-extended.yaml) incluye cuatro vistas creadas con tarjetas nativas de Home Assistant. Usa IDs de entidades de ejemplo: copia el YAML en un panel nuevo y asigna cada marcador a los IDs de tu propio registro de entidades. Sigue la [guía del panel](docs/dashboard-examples.md); no sustituyas la configuración de un panel existente.

## Actualizar desde 0.7.x

La versión 0.8.0 retiró los dos contadores antiguos sin versión de permitidas/bloqueadas por cliente. La integración restaura **Consultas (último minuto)** con su identidad e historial originales y archiva las antiguas tasas por minuto de permitidas/bloqueadas, conservando sus entradas de registro y Recorder. Actualiza paneles y automatizaciones de los contadores clasificados para que utilicen **Consultas permitidas** y **Consultas bloqueadas**. Las instalaciones nuevas crean cinco sensores por cliente.

Con HACS, actualiza la integración y reinicia cuando se indique. Conserva la entrada de integración para mantener su cursor y contadores. Si cambias el endpoint de Pi-hole, usa **Reconfigurar** y elige **Mismo Pi-hole** si sigue siendo el mismo servidor; así conserva su identidad de origen y cursor. Elige **Pi-hole de reemplazo** solo cuando hayas cambiado el servidor, para iniciar una generación de origen nueva.

Antes de volver a una versión anterior a la 0.8.0, restaura una copia de seguridad completa de Home Assistant compatible junto con los archivos de integración correspondientes. Revertir solo el código de la integración no deshace las migraciones de configuración y Store.

## Solución de problemas

- **Los contadores no aumentan:** revisa el estado de comunicación, host, puerto, ajustes TLS, contraseña, intervalo de sondeo y si Pi-hole aún expone esa consulta como nueva en su historial en memoria.
- **Aumentan los contadores, pero Activity está vacío:** confirma que esté seleccionado el modo Detallado, que Activity permita Todos o Solo bloqueados según el resultado, y que los filtros de Activity/Recorder de Home Assistant permitan el evento. Home Assistant controla la persistencia; la integración no la verifica.
- **El modo Agregado se retrasa con mucho tráfico sostenido:** puede acumular retraso cuando recibe observaciones continuamente. Consulta las mediciones sintéticas y sus límites en los [detalles técnicos](docs/DETALLES_ES.md).

## Enlaces

- [Notas de la versión](https://github.com/Andresako/pi-hole-extended/releases/tag/0.8.0) · [Todas las versiones](https://github.com/Andresako/pi-hole-extended/releases)
- [Guía del panel](docs/dashboard-examples.md) · [Detalles técnicos y limitaciones](docs/DETALLES_ES.md)
- [Informar de un problema](https://github.com/Andresako/pi-hole-extended/issues) · [Política de seguridad](SECURITY.md) · [Licencia MIT](LICENSE)
- [Read this guide in English](README.md)
