import streamlit as st
import requests
import pandas as pd
import folium
from streamlit_folium import st_folium
import altair as alt

# ==========================================
# GESTIONE DATI GEOGRAFICI E POPOLAZIONE
# ==========================================

@st.cache_data(show_spinner=False)
def ottieni_nome_comune(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10"
        headers = {'User-Agent': 'Streamlit-Energy-App'}
        res = requests.get(url, headers=headers).json()
        address = res.get('address', {})
        return address.get('city', address.get('town', address.get('village', address.get('county', 'Comune Selezionato'))))
    except:
        return "Comune Selezionato"

@st.cache_data(show_spinner=False)
def ottieni_popolazione(nome_comune):
    if nome_comune == "Comune Selezionato" or not nome_comune:
        return 5000
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={nome_comune}&count=1&language=it"
        res = requests.get(url).json()
        if "results" in res and len(res["results"]) > 0:
            popolazione = res["results"][0].get("population")
            if popolazione and popolazione > 0:
                return int(popolazione)
    except Exception as e:
        print(f"Errore API Popolazione: {e}")
    return 5000 

# ==========================================
# FUNZIONI DI CALCOLO ENERGETICO
# ==========================================

@st.cache_data(show_spinner=False)
def scarica_profili_fotovoltaico(lat, lon):
    url_pv = "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc"
    params_pv = {
        "lat": lat, "lon": lon, "pvcalculation": 1,
        "peakpower": 1.0, "loss": 14.0, "outputformat": "json", 
        "startyear": 2017, "endyear": 2020, "optimalangles": 1 
    }
    resp_pv = requests.get(url_pv, params=params_pv)
    if resp_pv.status_code != 200: return None
    
    data_pv = resp_pv.json()
    df_pv = pd.DataFrame(data_pv['outputs']['hourly'])
    df_pv['Data_Ora'] = pd.to_datetime(df_pv['time'], format='%Y%m%d:%H%M').dt.floor('h')
    df_pv['FV_Normalizzato'] = df_pv['P'] / 1000.0 
    return df_pv[['Data_Ora', 'FV_Normalizzato']]

@st.cache_data(show_spinner=False)
def esegui_simulazione_comunale(df_energia, cap_pv_mw, carico_medio_mw, cap_batt_mwh):
    soc = 0.0  
    tot_fv_mwh, tot_richiesta_mwh = 0.0, 0.0
    energia_fornita_fv_mwh, energia_fornita_batt_mwh = 0.0, 0.0
    energia_prelevata_rete_mwh, energia_scartata_mwh = 0.0, 0.0
    
    storia_fv_diretto, storia_batt_scarica, storia_rete = [], [], []
    
    for _, row in df_energia.iterrows():
        p_fv = row['FV_Normalizzato'] * cap_pv_mw
        
        tot_fv_mwh += p_fv
        tot_richiesta_mwh += carico_medio_mw
        
        uso_diretto = min(p_fv, carico_medio_mw)
        carico_residuo = carico_medio_mw - uso_diretto
        energia_eccedente = p_fv - uso_diretto
        
        energia_fornita_fv_mwh += uso_diretto
            
        if energia_eccedente > 0:
            spazio = cap_batt_mwh - soc
            immessa = min(energia_eccedente, spazio)
            soc += immessa
            energia_scartata_mwh += (energia_eccedente - immessa)
            
        batt_usata = 0.0
        if carico_residuo > 0:
            batt_usata = min(soc, carico_residuo)
            soc -= batt_usata
            carico_residuo -= batt_usata
            energia_fornita_batt_mwh += batt_usata
            
        rete_usata = 0.0
        if carico_residuo > 0:
            rete_usata = carico_residuo
            energia_prelevata_rete_mwh += rete_usata
            
        storia_fv_diretto.append(uso_diretto)
        storia_batt_scarica.append(batt_usata)
        storia_rete.append(rete_usata)

    autonomia = ((energia_fornita_fv_mwh + energia_fornita_batt_mwh) / tot_richiesta_mwh) * 100
    
    return {
        "autonomia": autonomia, "fv_mwh": tot_fv_mwh,
        "richiesta_mwh": tot_richiesta_mwh, "rete_mwh": energia_prelevata_rete_mwh,
        "scarto_mwh": energia_scartata_mwh, "storia_fv": storia_fv_diretto,
        "storia_batt": storia_batt_scarica, "storia_rete": storia_rete
    }

# ==========================================
# INTERFACCIA STREAMLIT
# ==========================================

st.set_page_config(page_title="Pianificatore FV Comunale", layout="wide")
st.title("🏙️ Pianificatore di Indipendenza Energetica Comunale")

with st.expander("📖 README - Informazioni sul progetto"):
    st.markdown("""
    Questo strumento è stato creato da **Giovanni Ludovico Montagnani** per aiutare i Comuni e le amministrazioni locali a capire 
    esattamente quanto fotovoltaico e quanta capacità di accumulo servirebbero per raggiungere una reale e solida **autonomia energetica**.
    
    Spesso si fatica a visualizzare la transizione ecologica su scala locale. Questo simulatore traduce il fabbisogno energetico in 
    grandezze fisiche intuitive (ettari di terreno o tetti da coprire) e calcola in modo realistico la dipendenza dalla rete, lo spreco (curtailment) 
    e il ritorno economico (Payback), basandosi su 4 anni di dati meteorologici orari reali forniti dai database europei (PVGIS).
    """)

if "lat" not in st.session_state: 
    st.session_state.lat, st.session_state.lon = 41.9028, 12.4964
    st.session_state.nome_comune = "Roma"
    st.session_state.popolazione = 2749031

col1, col2 = st.columns([1, 1.2])

with col1:
    st.subheader(f"📍 Posizione: {st.session_state.nome_comune}")
    m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=11)
    folium.Marker([st.session_state.lat, st.session_state.lon], tooltip=st.session_state.nome_comune).add_to(m)
    mappa = st_folium(m, height=350, use_container_width=True)
    
    if mappa and mappa.get("last_clicked"):
        st.session_state.lat = mappa["last_clicked"]["lat"]
        st.session_state.lon = mappa["last_clicked"]["lng"]
        
        with st.spinner("Ricerca comune e popolazione in corso..."):
            nuovo_comune = ottieni_nome_comune(st.session_state.lat, st.session_state.lon)
            st.session_state.nome_comune = nuovo_comune
            st.session_state.popolazione = ottieni_popolazione(nuovo_comune)
            
        st.rerun()

with col2:
    st.subheader("👥 Parametri del Territorio")
    popolazione = st.number_input(
        "Popolazione del Comune (Abitanti):", 
        min_value=10, max_value=10000000, 
        value=st.session_state.popolazione, 
        step=100
    )
    
    fabbisogno_annuo_mwh = popolazione * 6.0
    carico_medio_mw = fabbisogno_annuo_mwh / 8760.0
    st.info(f"⚡ **Fabbisogno Stimato:** {fabbisogno_annuo_mwh:,.0f} MWh/anno")
    
    st.markdown("---")
    st.subheader("☀️ Dimensionamento Impianti")
    
    pv_suggerito_mw = fabbisogno_annuo_mwh / 1200.0 
    ettari_suggeriti = pv_suggerito_mw * 1.2 
    
    ettari_selezionati = st.slider(
        "Superficie Fotovoltaica da Installare (Ettari):", 
        min_value=0.0, 
        max_value=max(10.0, float(round(ettari_suggeriti * 2))), 
        value=float(round(ettari_suggeriti)), 
        step=0.5
    )
    cap_pv_mw = ettari_selezionati / 1.2
    st.caption(f"⚡ *Corrisponde a circa **{cap_pv_mw:.1f} MWp** di potenza.*")
    
    ore_batteria = st.slider(
        "Capacità di Accumulo (Ore rispetto al picco FV):", 
        min_value=0.0, 
        max_value=12.0, 
        value=4.0, 
        step=0.5
    )
    batteria_mwh = cap_pv_mw * ore_batteria
    st.caption(f"🔋 *Corrisponde a un banco batterie da **{batteria_mwh:.1f} MWh**.*")

st.divider()

# ==========================================
# SEZIONE FINANZIARIA (CAPEX e ROI)
# ==========================================
st.subheader("💰 Analisi Finanziaria (CAPEX e Risparmio)")

costo_fv = cap_pv_mw * 1_000_000       # 1.000 €/kW = 1.000.000 €/MW
costo_batt = batteria_mwh * 150_000    # 150.000 €/MWh
costo_totale = costo_fv + costo_batt

def formatta_euro(cifra):
    return f"€ {cifra:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")

c_fin1, c_fin2, c_fin3 = st.columns(3)
c_fin1.metric("Pannelli FV (1.000 €/kW)", formatta_euro(costo_fv))
c_fin2.metric("Batterie (150.000 €/MWh)", formatta_euro(costo_batt))
c_fin3.metric("TOTALE IMPIANTO", formatta_euro(costo_totale))

st.markdown("<br>", unsafe_allow_html=True)

# Parametro dinamico per il costo dell'energia
prezzo_energia_kwh = st.number_input(
    "Prezzo dell'Energia dalla Rete (€/kWh):", 
    min_value=0.05, max_value=0.50, value=0.15, step=0.01,
    help="Inserisci il costo al kWh (es. 0,15 €). Questo equivale a 150 €/MWh e serve per calcolare il risparmio generato dall'impianto."
)
prezzo_energia_mwh = prezzo_energia_kwh * 1000

st.markdown("<br>", unsafe_allow_html=True)

esegui = st.button("🚀 Esegui Analisi di Copertura Energetica", use_container_width=True, type="primary")

if esegui:
    with st.spinner("Simulazione oraria in corso (analisi di 4 anni meteorologici da PVGIS)..."):
        df = scarica_profili_fotovoltaico(st.session_state.lat, st.session_state.lon)
        
        if df is not None:
            anni_simulati = len(df) / 8760.0
            res = esegui_simulazione_comunale(df, cap_pv_mw, carico_medio_mw, batteria_mwh)
            
            st.subheader(f"📊 Risultati per {st.session_state.nome_comune} (Media annua)")
            
            fv_annuo = res['fv_mwh'] / anni_simulati
            richiesta_annua = res['richiesta_mwh'] / anni_simulati
            rete_annua = res['rete_mwh'] / anni_simulati
            scarto_annuo = res['scarto_mwh'] / anni_simulati
            
            # Calcolo del ritorno sull'investimento
            energia_risparmiata_mwh = richiesta_annua - rete_annua
            risparmio_annuo_euro = energia_risparmiata_mwh * prezzo_energia_mwh
            payback_anni = costo_totale / risparmio_annuo_euro if risparmio_annuo_euro > 0 else 0
            
            # Metriche Energetiche
            m1, m2, m3 = st.columns(3)
            m1.metric("Indipendenza Energetica", f"{res['autonomia']:.1f}%")
            m2.metric("Dipendenza dalla Rete", f"{100 - res['autonomia']:.1f}%", delta_color="inverse")
            m3.metric("Energia Sovrappdotta (Persa)", f"{(scarto_annuo/fv_annuo)*100 if fv_annuo>0 else 0:.1f}%")
            
            st.markdown("---")
            
            # Metriche Finanziarie (ROI)
            st.write("### 💶 Ritorno sull'Investimento (ROI)")
            r1, r2, r3 = st.columns(3)
            r1.metric("Energia Risparmiata", f"{energia_risparmiata_mwh:,.0f} MWh/anno")
            r2.metric("Risparmio in Bolletta", formatta_euro(risparmio_annuo_euro))
            if payback_anni > 0:
                r3.metric("Tempo di Rientro (Payback)", f"{payback_anni:.1f} anni")
            else:
                r3.metric("Tempo di Rientro (Payback)", "N/A")

            st.markdown("---")
            st.subheader("📈 Andamento della Copertura Energetica")
            
            df_fonti = pd.DataFrame({
                "Data": df["Data_Ora"],
                "1. FV (Uso Diretto)": res["storia_fv"],
                "2. Batteria (Scarica)": res["storia_batt"],
                "3. Rete Nazionale": res["storia_rete"]
            })
            
            df_giornaliero_fonti = df_fonti.set_index("Data").resample('W').mean().reset_index()
            df_melted = df_giornaliero_fonti.melt(id_vars='Data', var_name='Fonte', value_name='MW')
            
            ordine_fonti = ["1. FV (Uso Diretto)", "2. Batteria (Scarica)", "3. Rete Nazionale"]
            colori_fonti = ["#FFC300", "#33CC33", "#666666"]
            
            chart = alt.Chart(df_melted).mark_area().encode(
                x=alt.X('Data:T', title='Data'),
                y=alt.Y('MW:Q', stack='zero', title='Potenza Media (MW)'),
                color=alt.Color('Fonte:N', scale=alt.Scale(domain=ordine_fonti, range=colori_fonti)),
                order=alt.Order('Fonte:N', sort='ascending')
            ).properties(height=450).interactive()
            
            st.altair_chart(chart, use_container_width=True)
