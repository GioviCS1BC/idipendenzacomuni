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
    """Usa Nominatim per ottenere il nome testuale del comune dalle coordinate."""
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
    """Interroga l'API di Open-Meteo Geocoding per ottenere gli abitanti reali del comune."""
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
# FUNZIONI DI CALCOLO ENERGETICO (PVGIS)
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
    batteria_mwh = carico_medio_mw * 4.0 
    
    st.info(f"⚡ **Fabbisogno Stimato:** {fabbisogno_annuo_mwh:,.0f} MWh/anno  \n"
            f"🔋 **Accumulo Richiesto (4 ore):** {batteria_mwh:,.1f} MWh")
    
    st.markdown("---")
    st.subheader("☀️ Dimensionamento Territoriale")
    
    # 1 MWp produce circa 1200 MWh/anno. Calcoliamo i MW necessari, poi gli ettari.
    pv_suggerito_mw = fabbisogno_annuo_mwh / 1200.0 
    ettari_suggeriti = pv_suggerito_mw * 1.2 # 1 MWp = 1.2 Ettari
    
    # Nuovo slider in Ettari
    ettari_selezionati = st.slider(
        "Superficie Fotovoltaica da Installare (Ettari):", 
        min_value=0.0, 
        max_value=max(10.0, float(round(ettari_suggeriti * 2))), 
        value=float(round(ettari_suggeriti)), 
        step=0.5
    )
    
    # Riconversione in Megawatt per il motore di calcolo
    cap_pv_mw = ettari_selezionati / 1.2
    st.caption(f"⚡ *{ettari_selezionati} ettari di suolo o tetti corrispondono a circa **{cap_pv_mw:.1f} MWp** di potenza.*")

st.divider()

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
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Indipendenza Energetica", f"{res['autonomia']:.1f}%")
            m2.metric("Dipendenza dalla Rete", f"{100 - res['autonomia']:.1f}%", delta_color="inverse")
            m3.metric("Energia Sovrappdotta (Persa)", f"{(scarto_annuo/fv_annuo)*100 if fv_annuo>0 else 0:.1f}%")
            
            st.markdown("---")
            c1, c2, c3 = st.columns(3)
            c1.write(f"**Produzione FV Media:** {fv_annuo:,.0f} MWh/anno")
            c2.write(f"**Acquisto Rete Nazionale:** {rete_annua:,.0f} MWh/anno")
            c3.write(f"**Energia Sprecata (Curtailment):** {scarto_annuo:,.0f} MWh/anno")
            
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
