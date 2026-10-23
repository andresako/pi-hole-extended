# Pi-hole Extended

Consulta la actividad DNS de **Pi-hole v6** en Home Assistant. La integración crea un dispositivo con sensores para cada cliente de Pi-hole. De forma predeterminada conserva detalles recientes y registra cada consulta nueva en Actividad/Logbook.

## Qué incluye

- Un dispositivo de Home Assistant por cliente.
- Seis sensores de contadores por cliente: consultas observadas, contadores legados de permitidas/bloqueadas y contadores permitidas/bloqueadas/provisionales de la versión activa del clasificador. Las observaciones del último minuto son atributos del sensor total, no entidades separadas.
- Entradas individuales en **Ajustes → Sistema → Registros → Actividad** de forma predeterminada.
- Nombres sencillos e IDs `phe_` para sensores por cliente; los IDs diagnósticos corresponden a cada entrada de la integración.

## Instalar con HACS

1. En HACS, abre **Integraciones → ⋮ → Repositorios personalizados**.
2. Añade `https://github.com/Andresako/pi-hole-extended` y elige la categoría **Integración**.
3. Instala **Pi-hole Extended** y reinicia Home Assistant.
4. Ve a **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Pi-hole Extended** e introduce los datos de Pi-hole.

Se instala desde un repositorio personalizado de HACS; actualmente no está en el catálogo predeterminado de HACS.

## Configuración

La integración requiere Home Assistant 2025.1 o posterior y Pi-hole v6. El contrato de consultas está contrastado con FTL 6.1.0–6.8.x; cada Pi-hole sirve su propia referencia de API en `/api/docs`. Crea una contraseña de aplicación desde la interfaz web de Pi-hole; solo se muestra una vez. Durante la configuración, indica:

- El nombre de host o la dirección IP de Pi-hole (sin `http://`, `https://`, puerto ni ruta) y su puerto web/API.
- Si Pi-hole usa HTTPS y si se debe verificar su certificado.
- La contraseña de aplicación.
- Cada cuánto Home Assistant consulta si hay consultas nuevas (15 segundos de forma predeterminada).

El puerto se usa exactamente como se indica: HTTPS no lo cambia silenciosamente a 443; conserva el puerto personalizado al cambiar el protocolo. La contraseña se guarda en la entrada de configuración de Home Assistant. No se incluye en este repositorio ni en los registros de la integración. Empieza con un intervalo de 5 segundos; reducirlo muestra antes las consultas nuevas y genera más solicitudes a la API. Consulta la [guía oficial de autenticación de Pi-hole](https://docs.pi-hole.net/api/auth/) para conocer el acceso a la API por sesión.

Tras configurar la integración, espera al menos un intervalo de sondeo y consulta los diagnósticos por entrada **Estado de comunicación** y **Cobertura de ingesta**. La primera lectura establece una línea base: no cuenta ni registra las consultas que ya están en la instantánea actual de Pi-hole. Para comprobar actividad nueva, genera una consulta DNS desde un cliente que use este Pi-hole y espera al siguiente sondeo. El total **Consultas DNS** del cliente debería aumentar; la entrada aparece en Activity solo con conservación **Detalle** y si la opción de Activity la permite. Una consulta bloqueada incrementa un contador de bloqueadas; una permitida, uno de permitidas; un estado desconocido o incompleto queda como provisional. El total cuenta observaciones recibidas por Home Assistant, no todas las consultas que atendió Pi-hole.

Si aumenta el total pero no aparece una entrada en Activity, comprueba que la conservación sea **Historial detallado**, que los eventos de Activity estén en **Todos** o **Bloqueados** según el resultado, y que cualquier filtro de inclusión de Activity permita el dominio `pihole_queries`. Los filtros de Recorder se aplican a los estados de entidades, no a este evento personalizado; si la configuración de Recorder excluye el tipo de evento `logbook_entry`, permítelo ahí. Home Assistant controla la persistencia de Activity/Recorder; la integración no la verifica. Si el total no aumenta, revisa los diagnósticos, endpoint, credenciales, intervalo y que la consulta siga disponible como nueva en el historial en memoria de Pi-hole.

Usa la acción **Reconfigurar** de la integración para cambiar el endpoint. Confirma **Mismo Pi-hole** para conservar su identidad, cursor y entidades, aunque cambien el protocolo o el puerto. Elige **Pi-hole de reemplazo** solo si se sustituyó el servidor: crea una generación y línea base nuevas, y deshabilita los sensores de cliente de la generación anterior conservando su historial de Recorder. Puedes dejar vacía la contraseña opcional para mantener la guardada.

## Conservación de consultas

En las opciones de la integración, elige **Historial detallado de consultas** o **Solo totales agregados**. El historial detallado es la opción predeterminada y conserva filas recientes para los sensores. El modo agregado conserva la identidad del cliente y los totales numéricos en el Store de la integración, sin dominios, IDs, estados brutos ni filas individuales; también desactiva las nuevas entradas de Activity. Con el historial detallado, la opción de Activity permite registrar todos los eventos (predeterminado), solo los bloqueados o ninguno. Estas opciones no cambian los contadores.

Al cambiar al modo agregado se eliminan los detalles de consultas del Store actual y de la copia heredada de la integración. No se borra el historial previo de Recorder o Activity, ni las exportaciones o copias de seguridad; Home Assistant puede seguir mostrando datos que ya se habían registrado.

## Actualizar o desinstalar

Con HACS, actualiza **Pi-hole Extended** desde HACS y reinicia Home Assistant cuando lo indique. En una instalación manual, sustituye `custom_components/pihole_queries` por los archivos nuevos y reinicia. Conserva la entrada de la integración para mantener su cursor y contadores. Antes de quitarla, anota su `entry_id` y, si existe, `source_id`; después elimina la entrada desde **Ajustes → Dispositivos y servicios**, desinstala la integración en HACS o borra su carpeta y reinicia Home Assistant. La entrada no elimina automáticamente los archivos Store de la integración en `.storage`. El modo detallado puede guardar la última consulta y hasta diez filas recientes por cliente; el modo agregado conserva el cursor y resúmenes numéricos. La integración no ofrece una acción para purgar el Store. Si también quieres borrar esos archivos, crea una copia de seguridad recuperable de Home Assistant, detén Home Assistant y lista los archivos cuyo nombre empieza por `pihole_queries_cursor_<source_id>_` de esa entrada y elimina solo esos archivos exactos, además de su archivo legado `pihole_queries_cursor_<entry_id>` si existe. Así incluyes generaciones anteriores de ese origen. No borres archivos de otros `source_id` o entradas ni el directorio `.storage`. Una entrada nueva tendrá otra identidad de origen y no reanudará el Store antiguo. Activity/Recorder ya guardados y las copias externas siguen las políticas de retención y copias de seguridad de Home Assistant.

## Más información

- [Technical details and limitations](docs/DETAILS.md)
- [Detalles técnicos y limitaciones](docs/DETALLES_ES.md)
- [Informar de un problema](https://github.com/Andresako/pi-hole-extended/issues)
