"""
Herramientas (tools) del agente para consultar y procesar datos de InfluxDB.

Cada herramienta tiene dos partes:
  1. Una función de Python que hace el trabajo.
  2. Una definición (schema JSON) que le dice al modelo qué hace y qué parámetros recibe.

Para agregar una herramienta nueva: escribe la función, agrega su schema a TOOLS
y regístrala en FUNCIONES.
"""
import re
import pandas as pd
import streamlit as st
from influxdb_client import InfluxDBClient

ZONA_HORARIA = "America/Bogota"

# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------
# Las credenciales vienen de los campos de la barra lateral (st.session_state["influx"]).
# Cada usuario de la app usa sus propias credenciales; no se guardan en disco.
@st.cache_resource(show_spinner=False)
def _crear_cliente(url: str, token: str, org: str) -> InfluxDBClient:
    return InfluxDBClient(url=url, token=token, org=org, timeout=30_000)


def _cfg() -> dict:
    cfg = st.session_state.get("influx", {})
    faltan = [k for k in ("url", "token", "org", "bucket") if not cfg.get(k)]
    if faltan:
        raise ValueError(f"Faltan credenciales de InfluxDB: {', '.join(faltan)}")
    return cfg


def get_client() -> InfluxDBClient:
    c = _cfg()
    return _crear_cliente(c["url"].rstrip("/"), c["token"], c["org"])


def _bucket() -> str:
    return _cfg()["bucket"]


# ---------------------------------------------------------------------------
# Validación de parámetros (evita inyectar Flux arbitrario)
# ---------------------------------------------------------------------------
RANGO_RE = re.compile(r"^-\d+(s|m|h|d|w|mo|y)$")
VENTANA_RE = re.compile(r"^\d+(s|m|h|d|w|mo)$")
AGREGACIONES = ["mean", "sum", "max", "min", "count", "median", "last"]
COLS_INTERNAS = ["result", "table", "_start", "_stop"]


def _esc(texto: str) -> str:
    return str(texto).replace("\\", "\\\\").replace('"', '\\"')


def _validar_rango(rango: str) -> str:
    if not RANGO_RE.match(rango):
        raise ValueError(f"Rango inválido '{rango}'. Usa formatos como -1h, -24h, -7d, -30d.")
    return rango


def _ejecutar(flux: str) -> pd.DataFrame:
    df = get_client().query_api().query_data_frame(flux)
    if isinstance(df, list):
        df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
    if not df.empty:
        df = df.drop(columns=[c for c in COLS_INTERNAS if c in df.columns])
        if "_time" in df.columns:
            df["_time"] = pd.to_datetime(df["_time"]).dt.tz_convert(ZONA_HORARIA)
    return df


def _filtro(measurement: str, field: str | None = None) -> str:
    f = f'|> filter(fn: (r) => r._measurement == "{_esc(measurement)}")'
    if field:
        f += f'\n  |> filter(fn: (r) => r._field == "{_esc(field)}")'
    return f


# ---------------------------------------------------------------------------
# 1. Descubrir la estructura del bucket
# ---------------------------------------------------------------------------
def listar_measurements() -> dict:
    flux = f'''
import "influxdata/influxdb/schema"
schema.measurements(bucket: "{_esc(_bucket())}")
'''
    df = _ejecutar(flux)
    return {"bucket": _bucket(), "measurements": df["_value"].tolist() if not df.empty else []}


def listar_campos(measurement: str) -> dict:
    b = _esc(_bucket())
    m = _esc(measurement)
    campos = _ejecutar(f'''
import "influxdata/influxdb/schema"
schema.measurementFieldKeys(bucket: "{b}", measurement: "{m}")
''')
    tags = _ejecutar(f'''
import "influxdata/influxdb/schema"
schema.measurementTagKeys(bucket: "{b}", measurement: "{m}")
''')
    tags_utiles = [t for t in (tags["_value"].tolist() if not tags.empty else [])
                   if not t.startswith("_")]
    return {
        "measurement": measurement,
        "campos": campos["_value"].tolist() if not campos.empty else [],
        "tags": tags_utiles,
    }


# ---------------------------------------------------------------------------
# 2. Consultar datos
# ---------------------------------------------------------------------------
def ultimo_valor(measurement: str, field: str) -> dict:
    flux = f'''
from(bucket: "{_esc(_bucket())}")
  |> range(start: -30d)
  {_filtro(measurement, field)}
  |> last()
'''
    df = _ejecutar(flux)
    if df.empty:
        return {"mensaje": "No hay datos en los últimos 30 días para ese campo."}
    filas = df.to_dict(orient="records")
    for f in filas:
        f["_time"] = str(f["_time"])
    return {"ultimos_valores": filas}


def consultar_serie(measurement: str, field: str, rango: str = "-24h",
                    ventana: str = "1h", agregacion: str = "mean") -> dict:
    _validar_rango(rango)
    if not VENTANA_RE.match(ventana):
        raise ValueError(f"Ventana inválida '{ventana}'. Usa 5m, 15m, 1h, 1d...")
    if agregacion not in AGREGACIONES:
        raise ValueError(f"Agregación inválida. Opciones: {AGREGACIONES}")

    flux = f'''
from(bucket: "{_esc(_bucket())}")
  |> range(start: {rango})
  {_filtro(measurement, field)}
  |> aggregateWindow(every: {ventana}, fn: {agregacion}, createEmpty: false)
'''
    df = _ejecutar(flux)
    if df.empty:
        return {"mensaje": "La consulta no devolvió datos.", "_df": df}

    muestra = df.tail(48).copy()
    muestra["_time"] = muestra["_time"].astype(str)
    return {
        "puntos": len(df),
        "desde": str(df["_time"].min()),
        "hasta": str(df["_time"].max()),
        "ultimos_puntos": muestra.to_dict(orient="records"),
        "_df": df,  # se usa para graficar en la app; no se envía al modelo
    }


# ---------------------------------------------------------------------------
# 3. Procesar datos
# ---------------------------------------------------------------------------
def estadisticas(measurement: str, field: str, rango: str = "-24h") -> dict:
    _validar_rango(rango)
    flux = f'''
from(bucket: "{_esc(_bucket())}")
  |> range(start: {rango})
  {_filtro(measurement, field)}
  |> keep(columns: ["_time", "_value"])
  |> limit(n: 200000)
'''
    df = _ejecutar(flux)
    if df.empty:
        return {"mensaje": "No hay datos en ese rango."}
    v = pd.to_numeric(df["_value"], errors="coerce").dropna()
    i_max, i_min = v.idxmax(), v.idxmin()
    return {
        "n": int(v.count()),
        "media": round(float(v.mean()), 4),
        "mediana": round(float(v.median()), 4),
        "desv_std": round(float(v.std()), 4),
        "minimo": round(float(v.min()), 4),
        "hora_minimo": str(df.loc[i_min, "_time"]),
        "maximo": round(float(v.max()), 4),
        "hora_maximo": str(df.loc[i_max, "_time"]),
        "desde": str(df["_time"].min()),
        "hasta": str(df["_time"].max()),
    }


def perfil_horario(measurement: str, field: str, rango: str = "-7d") -> dict:
    """Promedio por hora del día: útil para ver a qué horas se consume más."""
    _validar_rango(rango)
    flux = f'''
from(bucket: "{_esc(_bucket())}")
  |> range(start: {rango})
  {_filtro(measurement, field)}
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> keep(columns: ["_time", "_value"])
'''
    df = _ejecutar(flux)
    if df.empty:
        return {"mensaje": "No hay datos en ese rango."}
    df["hora"] = df["_time"].dt.hour
    perfil = df.groupby("hora")["_value"].mean().round(4)
    tabla = perfil.reset_index().rename(columns={"_value": "promedio"})
    return {
        "perfil": perfil.to_dict(),
        "hora_pico": int(perfil.idxmax()),
        "hora_valle": int(perfil.idxmin()),
        "_df": tabla,
    }


# ---------------------------------------------------------------------------
# Definiciones que ve el modelo
# ---------------------------------------------------------------------------
_MF = {
    "measurement": {"type": "string", "description": "Nombre del measurement"},
    "field": {"type": "string", "description": "Nombre del campo (_field)"},
}
_RANGO = {"type": "string",
          "description": "Rango relativo hacia atrás: -1h, -24h, -7d, -30d"}

TOOLS = [
    {
        "name": "listar_measurements",
        "description": "Lista los measurements del bucket. Úsala primero si no conoces la estructura.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "listar_campos",
        "description": "Lista los campos (fields) y tags de un measurement.",
        "input_schema": {"type": "object", "properties": {"measurement": _MF["measurement"]},
                         "required": ["measurement"]},
    },
    {
        "name": "ultimo_valor",
        "description": "Devuelve el último valor registrado de un campo y su hora.",
        "input_schema": {"type": "object", "properties": _MF, "required": ["measurement", "field"]},
    },
    {
        "name": "consultar_serie",
        "description": "Consulta una serie de tiempo agregada por ventanas. Se grafica en la app.",
        "input_schema": {
            "type": "object",
            "properties": {
                **_MF,
                "rango": _RANGO,
                "ventana": {"type": "string", "description": "Tamaño de ventana: 5m, 15m, 1h, 1d"},
                "agregacion": {"type": "string", "enum": AGREGACIONES},
            },
            "required": ["measurement", "field"],
        },
    },
    {
        "name": "estadisticas",
        "description": "Estadísticas descriptivas (media, mediana, std, mín, máx y sus horas) de un campo.",
        "input_schema": {"type": "object", "properties": {**_MF, "rango": _RANGO},
                         "required": ["measurement", "field"]},
    },
    {
        "name": "perfil_horario",
        "description": "Promedio por hora del día (0-23) para identificar horas pico y valle.",
        "input_schema": {"type": "object", "properties": {**_MF, "rango": _RANGO},
                         "required": ["measurement", "field"]},
    },
]

FUNCIONES = {
    "listar_measurements": listar_measurements,
    "listar_campos": listar_campos,
    "ultimo_valor": ultimo_valor,
    "consultar_serie": consultar_serie,
    "estadisticas": estadisticas,
    "perfil_horario": perfil_horario,
}


def ejecutar_tool(nombre: str, args: dict) -> tuple[dict, pd.DataFrame | None]:
    """Ejecuta una tool. Devuelve (resultado_para_el_modelo, dataframe_para_graficar)."""
    try:
        resultado = FUNCIONES[nombre](**args)
    except Exception as e:  # el error vuelve al modelo para que corrija
        return {"error": f"{type(e).__name__}: {e}"}, None
    df = resultado.pop("_df", None)
    return resultado, df
