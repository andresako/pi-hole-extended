# Pi-hole Extended

Consulta en Home Assistant las consultas DNS individuales de **Pi-hole v6**. La integración registra las consultas nuevas en Actividad/Logbook y crea un dispositivo con sensores para cada cliente de Pi-hole.

## Qué incluye

- Un dispositivo de Home Assistant por cliente.
- Seis sensores por cliente: consultas totales, permitidas y bloqueadas, y esos mismos recuentos durante el último minuto.
- Entradas individuales de consultas en **Ajustes → Sistema → Registros → Actividad**.
- Nombres sencillos y IDs con prefijo `phe_`, fáciles de filtrar en Recorder.

## Instalar con HACS

1. En HACS, abre **Integraciones → ⋮ → Repositorios personalizados**.
2. Añade `https://github.com/Andresako/pi-hole-extended` y elige la categoría **Integración**.
3. Instala **Pi-hole Extended** y reinicia Home Assistant.
4. Ve a **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Pi-hole Extended** e introduce los datos de Pi-hole.

Se instala desde un repositorio personalizado de HACS; actualmente no está en el catálogo predeterminado de HACS.

## Configuración

Necesitas Pi-hole v6 y una contraseña de aplicación creada en Pi-hole. Durante la configuración, indica:

- El host y el puerto web/API de Pi-hole.
- Si Pi-hole usa HTTPS y si se debe verificar su certificado.
- La contraseña de aplicación.
- Cada cuánto Home Assistant consulta si hay consultas nuevas (15 segundos de forma predeterminada).

La contraseña se guarda en la entrada de configuración de Home Assistant. No se incluye en este repositorio ni en los registros de la integración. Empieza con un intervalo de 5 segundos; reducirlo muestra antes las consultas nuevas y genera más solicitudes a la API.

## Más información

- [Technical details and limitations](docs/DETAILS.md)
- [Detalles técnicos y limitaciones](docs/DETALLES_ES.md)
- [Informar de un problema](https://github.com/Andresako/pi-hole-extended/issues)
