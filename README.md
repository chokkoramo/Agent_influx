# ⚡ Agente InfluxDB — Consumo eléctrico

Agente simple en Streamlit que responde preguntas sobre datos de InfluxDB usando *tool use* de Claude.

## Estructura

```
app.py            Interfaz Streamlit + ciclo del agente
tools.py          Herramientas (funciones + definiciones para el modelo)
requirements.txt  Dependencias
```

## Cómo funciona el agente

1. El usuario pregunta en el chat.
2. El modelo decide qué herramienta usar y con qué parámetros.
3. `app.py` ejecuta la herramienta contra InfluxDB y le devuelve el resultado.
4. Se repite hasta que el modelo tiene la respuesta final (máx. 8 pasos).

## Herramientas

| Tool | Qué hace |
|---|---|
| `listar_measurements` | Measurements del bucket |
| `listar_campos` | Fields y tags de un measurement |
| `ultimo_valor` | Última lectura de un campo |
| `consultar_serie` | Serie agregada por ventana (se grafica) |
| `estadisticas` | Media, mediana, std, mín/máx con su hora |
| `perfil_horario` | Promedio por hora del día: pico y valle (se grafica) |

Para agregar una: escribe la función en `tools.py`, añade su schema a `TOOLS` y regístrala en `FUNCIONES`.

## Credenciales

Se ingresan en la barra lateral (token y API key como campos ocultos). Viven solo en la sesión del navegador; no se guardan en disco ni en el repositorio.

## Despliegue en Streamlit Cloud

1. Sube estos tres archivos a un repositorio de GitHub.
2. En share.streamlit.io: **New app** → repo → archivo principal `app.py` → **Deploy**.
3. Abre la app, ingresa token y API key, y pulsa **Probar conexión**.

## Ejecutar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```
