# -*- coding: utf-8 -*-
import requests
import os
import json
import logging
from datetime import datetime
import urllib3

# --- Configurazione Stazioni ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
URL_STAZIONI = "https://retemir.regione.marche.it/api/stations/rt-data"
STAZIONI_INTERESSATE = [
    "Misa", "Pianello di Ostra", "Nevola", "Barbara",
    "Serra dei Conti", "Arcevia", "Corinaldo", "Ponte Garibaldi", "Senigallia","Passo Ripe",
]
CODICE_ARCEVIA_CORRETTO = 732
# Non serve più SENSORI_INTERESSATI_TIPOSENS se processiamo tutti quelli con soglia
# SENSORI_INTERESSATI_TIPOSENS = [0, 1, 100, 101] # Manteniamolo per chiarezza iniziale nel ciclo
DESCRIZIONI_SENSORI = {
    0: "Pioggia TOT Oggi", 1: "Intensità Pioggia mm/min", 5: "Temperatura Aria",
    6: "Umidità Relativa", 8: "Pressione Atmosferica", 9: "Direzione Vento",
    10: "Velocità Vento", 100: "Livello Idrometrico", 101: "Livello Idrometrico 2",
    7: "Radiazione Globale", 107: "Livello Neve"
}
SOGLIE_GENERICHE = { 0: 15.0, 1: 0.25, 5: 35.0, 10: 15.0 }
SOGLIE_PER_STAZIONE = {
    "Nevola": { 100: 2.0, 1: 0.25 }, "Misa": { 100: 2.0 },
    "Ponte Garibaldi": { 101: 2.2 }, "Passo Ripe": { 100: 1.2 },
    "Serra dei Conti": { 100: 1.7 }
}
# Aggiungiamo i tipi di sensore idrometrici per cui vogliamo il trend
SENSORI_IDROMETRICI_TREND = [100, 101]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Funzioni Helper (fetch_data, send_telegram_message - invariate) ---
def fetch_data(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    response = None
    try:
        logging.warning(f"[Full Report Script] Tentativo richiesta STAZIONI a {url} con verify=False.")
        response = requests.get(url, headers=headers, timeout=45, verify=False)
        logging.info(f"[Full Report Script] Richiesta STAZIONI a {url} - Status: {response.status_code}")
        response.raise_for_status(); return response.json()
    except requests.exceptions.Timeout as e: logging.error(f"[Full Report Script] Timeout: {e}"); return None
    except requests.exceptions.HTTPError as e: logging.error(f"[Full Report Script] Errore HTTP: {e.response.status_code} - {e.response.text[:200]}..."); return None
    except requests.exceptions.ConnectionError as e: logging.error(f"[Full Report Script] Errore Conn: {e}"); return None
    except requests.exceptions.RequestException as e: logging.error(f"[Full Report Script] Errore Req: {e}"); return None
    except json.JSONDecodeError as e:
        resp_text=response.text[:200] if response else "N/A"; resp_status=response.status_code if response else "N/A"
        logging.error(f"[Full Report Script] Errore JSON: Status {resp_status}, Resp '{resp_text}...', Err: {e}"); return None
    except Exception as e: logging.error(f"[Full Report Script] Errore Imprevisto Fetch: {e}", exc_info=True); return None

def send_telegram_message(token, chat_id, text):
    if not token or not chat_id: logging.error("[Full Report Script] Credenziali mancanti."); return False
    max_length=4096
    if len(text)>max_length: text=text[:max_length-3]+"..."
    url=f"https://api.telegram.org/bot{token}/sendMessage"
    payload={'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
    try:
        response=requests.post(url, data=payload, timeout=15)
        response.raise_for_status(); logging.info(f"[Full Report Script] Msg inviato a {chat_id}"); return True
    except requests.exceptions.RequestException as e:
        logging.error(f"[Full Report Script] Errore invio TG: {e}")
        if e.response is not None: logging.error(f"[Full Report Script] Risposta API TG: {e.response.status_code} - {e.response.text}")
        return False

# --- Logica Principale Full Report ---
def check_stazioni_full_report():
    """Controlla stazioni e restituisce tupla: (soglie_superate, valori_attuali, errore_fetch)."""
    messaggi_soglia = []
    valori_attuali_monitorati = []
    logging.info(f"[Full Report Script] Controllo dati stazioni da {URL_STAZIONI}...")
    data = fetch_data(URL_STAZIONI)
    if data is None: return ([], [], "⚠️ Impossibile recuperare dati stazioni meteo.")

    stazioni_trovate_interessanti = False
    for stazione in data:
        # Filtro stazioni
        nome_stazione_raw = stazione.get("nome", "N/A"); nome_stazione = nome_stazione_raw.strip()
        codice_stazione = stazione.get("codice"); is_arcevia = "Arcevia" in nome_stazione_raw
        if not ((is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO) or \
                (not is_arcevia and nome_stazione in STAZIONI_INTERESSATE)): continue
        stazioni_trovate_interessanti = True

        sensori = stazione.get("analog", []); last_update = stazione.get("lastUpdateTime", "N/A")
        valori_stazione_str = f"*{nome_stazione}* (Agg: {last_update}):\n"; ha_valori_monitorati = False
        if not sensori: logging.debug(f"[Full Report Script] Nessun sensore per {nome_stazione}"); continue

        for sensore in sensori:
            tipoSens = sensore.get("tipoSens")

            # Determina la soglia applicabile (se esiste)
            soglia_da_usare = None; sorgente_soglia = "Nessuna"
            if nome_stazione in SOGLIE_PER_STAZIONE and tipoSens in SOGLIE_PER_STAZIONE[nome_stazione]:
                soglia_da_usare = SOGLIE_PER_STAZIONE[nome_stazione][tipoSens]; sorgente_soglia = f"Specifica ({nome_stazione})"
            elif tipoSens in SOGLIE_GENERICHE:
                 # Solo se il tipoSens è anche nella lista generale (opzionale, ma mantiene il comportamento precedente)
                 # if tipoSens in SENSORI_INTERESSATI_TIPOSENS: # Rimuovi questa riga se vuoi monitorare TUTTI i sensori con soglia generica
                    soglia_da_usare = SOGLIE_GENERICHE[tipoSens]; sorgente_soglia = "Generica"

            # Processa solo i sensori per cui abbiamo definito una soglia (quindi sono monitorati)
            if soglia_da_usare is not None:
                 valore_str = sensore.get("valore"); descr_sens = sensore.get("descr", DESCRIZIONI_SENSORI.get(tipoSens, f"Sensore {tipoSens}")).strip()
                 unmis = sensore.get("unmis", "").strip(); valore_display = "N/D"
                 trend_symbol = "" # Inizializza simbolo trend

                 try:
                     if valore_str is not None and valore_str != "" and valore_str.lower() != 'nan':
                         valore_num = float(valore_str); valore_display = f"{valore_num} {unmis}"

                         # --- INIZIO LOGICA TREND ---
                         if tipoSens in SENSORI_IDROMETRICI_TREND: # Controlla se è un sensore per cui vogliamo il trend
                             trend_raw = sensore.get("trend")
                             if trend_raw is not None:
                                 try:
                                     trend_num = float(trend_raw)
                                     # Usiamo una piccola tolleranza per confronti float con zero
                                     if trend_num > 1e-9:
                                         trend_symbol = "📈" # Positivo
                                     elif trend_num < -1e-9:
                                         trend_symbol = "📉" # Negativo
                                     else:
                                         trend_symbol = "➡️" # Stabile / Zero
                                 except (ValueError, TypeError):
                                     logging.warning(f"[Full Report Script] Err conversione trend '{trend_raw}' sensore {tipoSens} staz {nome_stazione}")
                                     trend_symbol = "❓" # Simbolo per errore trend
                             else:
                                 trend_symbol = "➡️" # Trend nullo = Stabile
                         # --- FINE LOGICA TREND ---

                         # Controllo soglia superata
                         if valore_num > soglia_da_usare:
                             # Aggiungi simbolo trend al messaggio di soglia superata
                             trend_display_soglia = f" {trend_symbol}" if trend_symbol else ""
                             msg_soglia = (f"‼️ *Soglia Superata!* ({sorgente_soglia})\n" # Cambiato Emoji
                                           f"   Stazione: *{nome_stazione}*\n"
                                           f"   Sensore: {descr_sens}\n"
                                           f"   Valore: *{valore_display}{trend_display_soglia}* (Soglia: {soglia_da_usare} {unmis})\n"
                                           f"   Ultimo Agg.: {last_update}")
                             messaggi_soglia.append(msg_soglia)
                             logging.warning(f"[Full Report Script] SOGLIA SUPERATA ({sorgente_soglia}): {nome_stazione} - {descr_sens} = {valore_num}{trend_display_soglia} > {soglia_da_usare}")

                     # Aggiungi valore corrente (e trend se applicabile) alla stringa
                     # Metti uno spazio prima del simbolo solo se esiste
                     trend_display_valore = f" {trend_symbol}" if trend_symbol else ""
                     valori_stazione_str += f"  - {descr_sens}: *{valore_display}{trend_display_valore}* (Soglia: {soglia_da_usare} {unmis})\n"
                     ha_valori_monitorati = True

                 except (ValueError, TypeError) as e:
                     # Valore non numerico, logga errore e mostra valore originale
                     logging.warning(f"[Full Report Script] Err conversione valore '{valore_str}' sensore {tipoSens} staz {nome_stazione}: {e}")
                     valori_stazione_str += f"  - {descr_sens}: *{valore_str}* (Val non num, Soglia: {soglia_da_usare} {unmis})\n"
                     ha_valori_monitorati = True

        # Aggiungi la stringa formattata della stazione solo se ha sensori monitorati
        if ha_valori_monitorati: valori_attuali_monitorati.append(valori_stazione_str.strip())

    if not stazioni_trovate_interessanti: logging.info(f"[Full Report Script] Nessuna stazione di interesse trovata tra quelle attive.")
    return (messaggi_soglia, valori_attuali_monitorati, None) # Restituisce tutto

# --- Esecuzione Script Full Report (invariata) ---
if __name__ == "__main__":
    logging.info("--- [Full Report Script] Avvio Controllo Stazioni ---")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.critical("[Full Report Script] Errore: Credenziali Telegram mancanti."); exit(1)

    lista_soglie_superate, lista_valori_attuali, errore_fetch = check_stazioni_full_report()
    messaggio_finale = ""; timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    header_base = f"*{'='*5} Report Stazioni ({timestamp}) {'='*5}*\n\n"; footer = f"\n\n*{'='*30}*"

    if errore_fetch: # Caso Errore Fetch
        messaggio_finale = header_base + errore_fetch + footer
        logging.error(f"[Full Report Script] Invio errore fetch: {errore_fetch}")
    else: # Caso Fetch OK
        sezioni_messaggio = []
        if lista_soglie_superate: # Aggiungi soglie superate
            sezioni_messaggio.append("*--- ‼️ SOGLIE SUPERATE ‼️ ---*"); sezioni_messaggio.extend(lista_soglie_superate); sezioni_messaggio.append(" ") # Aggiunto spazio e emoji
        if lista_valori_attuali: # Aggiungi valori attuali
            sezioni_messaggio.append("*--- VALORI ATTUALI MONITORATI ---*"); sezioni_messaggio.extend(lista_valori_attuali)
        elif not lista_soglie_superate: # Se non c'è nulla da mostrare
             sezioni_messaggio.append("✅ Nessuna soglia superata e nessun dato monitorato rilevante al momento.") # Messaggio più specifico
        messaggio_finale = header_base + "\n".join(sezioni_messaggio) + footer
        logging.info("[Full Report Script] Invio report completo.")

    if messaggio_finale: send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_finale)
    else: logging.warning("[Full Report Script] Nessun messaggio finale preparato.")
    logging.info("--- [Full Report Script] Controllo Stazioni completato ---")
