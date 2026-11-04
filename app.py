"""
Agente simple para consultar datos de consumo eléctrico en InfluxDB.
Ciclo del agente:  pregunta -> el modelo elige tools -> se ejecutan -> el modelo responde.
"""

import json

import anthropic
import pandas as pd
import streamlit as st

from tools import TOOLS, ejecutar_tool, listar_measurements

st.set_page_config(page_title="Agente InfluxDB", page_icon="⚡", layout="wide")

MAX_PASOS = 8
MODELOS = ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"]

SYSTEM = """Eres un asistente de análisis de datos de consumo eléctrico almacenados en InfluxDB.
Responde en español, de forma breve y clara, con unidades cuando las conozcas.
Si no conoces los measurements o campos, descúbrelos primero con listar_measurements y listar_campos.
No inventes datos: todo número que reportes debe venir de una herramienta.
Las horas están en hora de Colombia (America/Bogota)."""


@st.cache_resource(show_spinner=False)
def get_llm(api_key: str):
    return anthropic.Anthropic(api_key=api_key)


def graficar(df: pd.DataFrame):
    if df is None or df.empty:
        return
    if "hora" in df.columns:  # perfil horario
        st.bar_chart(df, x="hora", y="promedio")
        return
    tags = [c for c in df.columns if c not in ("_time", "_value", "_measurement")]
    if tags:
        df = df.assign(serie=df[tags].astype(str).agg(" | ".join, axis=1))
        tabla = df.pivot_table(index="_time", columns="serie", values="_value")
    else:
        tabla = df.set_index("_time")[["_value"]]
    tab_lineas, tab_area = st.tabs(["📈 Líneas", "🌊 Área"])
    with tab_lineas:
        st.line_chart(tabla)
    with tab_area:
        st.area_chart(tabla)


def correr_agente(historial: list) -> tuple[str, list]:
    """Ejecuta el ciclo de tool use. Devuelve (texto_final, pasos)."""
    llm = get_llm(st.session_state.api_key)
    pasos = []
    tarifa = st.session_state.get("tarifa_kwh", 850.0)
    system_dinamico = (
        f"{SYSTEM}\n\n"
        f"La tarifa actual de energía es de ${tarifa} por kWh. "
        f"Úsala para calcular y estimar costos en dinero cuando sea requerido o el usuario lo solicite."
    )
    for _ in range(MAX_PASOS):
        resp = llm.messages.create(
            model=st.session_state.modelo,
            max_tokens=2048,
            system=system_dinamico,
            tools=TOOLS,
            messages=historial,
        )
        historial.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            texto = "".join(b.text for b in resp.content if b.type == "text")
            return texto, pasos

        resultados = []
        for bloque in resp.content:
            if bloque.type != "tool_use":
                continue
            salida, df = ejecutar_tool(bloque.name, bloque.input)
            pasos.append(
                {"tool": bloque.name, "args": bloque.input, "salida": salida, "df": df}
            )
            resultados.append(
                {
                    "type": "tool_result",
                    "tool_use_id": bloque.id,
                    "content": json.dumps(salida, ensure_ascii=False, default=str),
                    "is_error": "error" in salida,
                }
            )
        historial.append({"role": "user", "content": resultados})

    return "Se alcanzó el máximo de pasos sin una respuesta final.", pasos


def mostrar_pasos(pasos: list, key_prefix: str = ""):
    for p in pasos:
        with st.expander(
            f"🔧 {p['tool']}({', '.join(f'{k}={v}' for k, v in p['args'].items())})"
        ):
            st.json(p["salida"])
    for idx, p in enumerate(pasos):
        df = p.get("df")
        if df is not None and not df.empty:
            graficar(df)
            key_btn = (
                f"descarga_csv_{key_prefix}_{idx}"
                if key_prefix
                else f"descarga_csv_{idx}"
            )
            st.download_button(
                label="📥 Descargar datos (CSV)",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name=f"datos_paso_{idx}.csv",
                mime="text/csv",
                key=key_btn,
            )


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------
if "historial" not in st.session_state:
    st.session_state.historial = []  # mensajes en formato API
    st.session_state.chat = []  # lo que se muestra en pantalla
    st.session_state.tarifa_kwh = 850.0

# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚡ Agente InfluxDB")

    with st.expander("🔑 Conexión", expanded=True):
        st.markdown("**InfluxDB**")
        url = st.text_input(
            "URL", value="https://us-east-1-1.aws.cloud2.influxdata.com"
        )
        org = st.text_input("Org", value="cmcorrea@unal.edu.co")
        bucket = st.text_input("Bucket", value="Consumo_elec")
        token = st.text_input("Token", type="password")
        st.markdown("**Modelo (Anthropic)**")
        st.session_state.api_key = st.text_input("API key", type="password")
        st.session_state.modelo = st.selectbox("Modelo", MODELOS)
        st.caption(
            "Las credenciales solo viven en esta sesión del navegador; no se guardan."
        )

    st.session_state.tarifa_kwh = st.number_input(
        "Precio por kWh ($)", value=850.0
    )

    st.session_state.influx = {"url": url, "token": token, "org": org, "bucket": bucket}
    listo = all(st.session_state.influx.values()) and bool(st.session_state.api_key)

    if st.button("Probar conexión", use_container_width=True):
        try:
            r = listar_measurements()
            st.success(
                f"Conectado. Measurements: {', '.join(r['measurements']) or '(ninguno)'}"
            )
        except Exception as e:  # noqa: BLE001
            st.error(f"Error de conexión: {e}")

    st.subheader("Preguntas de ejemplo")
    ejemplos = [
        "¿Qué datos hay en el bucket?",
        "¿Cuál es el último valor registrado?",
        "Grafica el consumo de las últimas 24 horas por hora",
        "Dame estadísticas de la última semana",
        "¿A qué hora del día se consume más?",
    ]
    for e in ejemplos:
        if st.button(e, use_container_width=True):
            st.session_state.pregunta_pendiente = e

    if st.button("🗑️ Limpiar chat", use_container_width=True):
        st.session_state.historial = []
        st.session_state.chat = []
        st.rerun()

    if st.session_state.chat:
        lineas = []
        for msg in st.session_state.chat:
            rol = "Usuario" if msg["role"] == "user" else "Agente"
            lineas.append(f"{rol}: {msg['texto']}")
        historial_texto = "\n\n".join(lineas)
        st.download_button(
            label="📥 Exportar historial",
            data=historial_texto,
            file_name="analisis_consumo.txt",
            mime="text/plain",
            use_container_width=True,
        )

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
st.title("Asistente de consumo eléctrico")

for idx_msg, msg in enumerate(st.session_state.chat):
    with st.chat_message(msg["role"]):
        if msg.get("pasos"):
            mostrar_pasos(msg["pasos"], key_prefix=f"chat_{idx_msg}")
        st.markdown(msg["texto"])

if not listo:
    st.info(
        "Completa el token de InfluxDB y la API key en la barra lateral para empezar."
    )

pregunta = st.chat_input("Pregunta sobre tus datos...", disabled=not listo) or (
    st.session_state.pop("pregunta_pendiente", None) if listo else None
)

if pregunta:
    st.session_state.chat.append({"role": "user", "texto": pregunta})
    st.session_state.historial.append({"role": "user", "content": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)

    with st.chat_message("assistant"):
        with st.spinner("Consultando InfluxDB..."):
            n_previo = len(st.session_state.historial) - 1
            try:
                texto, pasos = correr_agente(st.session_state.historial)
            except anthropic.APIError as e:
                texto, pasos = f"Error con el modelo: {e}", []
                # descarta la pregunta fallida para no dejar el historial a medias
                del st.session_state.historial[n_previo:]
        mostrar_pasos(pasos, key_prefix=f"chat_{len(st.session_state.chat)}")
        st.markdown(texto)

    st.session_state.chat.append({"role": "assistant", "texto": texto, "pasos": pasos})
