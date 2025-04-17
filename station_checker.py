# -*- coding: utf-8 -*-
import requests
import os
import json
import logging
from datetime import datetime
import urllib3
from collections import defaultdict # Importato per la gestione dei bacini

# --- Configurazione Stazioni (Aggiornata) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
URL_STAZIONI = "https://retemir.regione.marche.it/api/stations/rt-data"

# Mappa delle stazioni ai rispettivi bacini (Aggiornata)
BACINI_STAZIONI = {
    # Bacino Misa
    "Arcevia": "Misa",
    "Serra dei Conti": "Misa",
    "Barbara": "Misa",
    "Pianello di Ostra": "Misa",
    "Misa": "Misa",
    "Senigallia": "Misa",
    "Ponte Garibaldi": "Misa",
    # Bacino Nevola
    "Corinaldo": "Nevola",
    "Nevola": "Nevola",
    "Passo Ripe": "Nevola",
    # Bacino Cesano
    "Cesano": "Cesano",
    "Foce Cesano": "Cesano" # Manteniamo "Foce Cesano" come nel primo script
}
# Lista stazioni di interesse (deriva direttamente dalla mappa dei bacini)
STAZIONI_INTERESSATE = list(BACINI_STAZIONI.keys())

# Definizione ordine stazioni per bacino (Aggiornato)
ORDINE_STAZIONI_PER_BACINO = {
    "Misa": ["Arcevia", "Serra dei Conti", "Barbara", "Pianello di Ostra", "Misa", "Senigallia", "Ponte Garibaldi"],
    "Nevola": ["Corinaldo", "Nevola", "Passo Ripe"],
    "Cesano": ["Cesano", "Foce Cesano"],
    "Altri Bacini": [] # Per eventuali stazioni non mappate
}

CODICE_ARCEVIA_CORRETTO = 732

# Descrizioni Sensori (Aggiornato)
DESCRIZIONI_SENSORI = {
    0: "Pioggia TOT Oggi", 1: "Intensità Pioggia mm/min", 5: "Temperatura Aria",
    6: "Umidità Relativa", 8: "Pressione Atmosferica", 9: "Direzione Vento",
    10: "Velocità Vento", 100: "Livello Idrometrico", 101: "Livello Idrometrico 2",
    7: "Radiazione Globale", 107: "Livello Neve"
}

# Soglie Generiche (Aggiornato)
SOGLIE_GENERICHE = {
    0: 15.0, # Aggiunta soglia generica per Pioggia TOT Oggi
    1: 0.25,
}

# Soglie Specifiche per Stazione (Aggiornato)
SOGLIE_PER_STAZIONE = {
    # Misa
    "Misa": { 100: 2.0 },
    "Ponte Garibaldi": { 101: 2.2 }, # Corretto 2.0 -> 2.2 come nel primo script
    "Serra dei Conti": { 100: 1.7 }, # Corretto 1.8 -> 1.7 come nel primo script
    #"Arcevia": { 100: 1.8 }, # Rimosso se non necessario o gestito genericamente
    # Nevola
    "Nevola": { 100: 2.0, 1: 0.25 }, # Mantenuta specifica per Intensità pioggia se serve, altrimenti verrà usata la generica
    "Passo Ripe": { 100: 1.2 }, # Aggiunta soglia Passo Ripe come nel primo script
    # Cesano - Aggiungere soglie se necessario
    # "Cesano": { 100: X.X },
    # "Foce Cesano": { 100: Y.Y },
}

# Sensori idrometrici per cui vogliamo il trend (Aggiunto)
SENSORI_IDROMETRICI_TREND = [100, 101]
# Ordine desiderato per la visualizzazione dei bacini nel messaggio (Aggiunto)
ORDINE_BACINI = ["Misa", "Nevola", "Cesano", "Altri Bacini"]

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# Disabilita avvisi SSL per verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Funzioni Helper (Invariate rispetto al primo script modificato) ---

def fetch_data(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    response = None
    try:
        # Usiamo "Alert Script" nei log per distinguerlo
        logging.warning(f"[Alert Script] Tentativo richiesta STAZIONI a {url} con verify=False.")
        response = requests.get(url, headers=headers, timeout=45, verify=False)
        logging.info(f"[Alert Script] Richiesta STAZIONI a {url} - Status: {response.status_code}")
        response.raise_for_status(); return response.json()
    except requests.exceptions.Timeout as e: logging.error(f"[Alert Script] Timeout: {e}"); return None
    except requests.exceptions.HTTPError as e: logging.error(f"[Alert Script] Errore HTTP: {e.response.status_code} - {e.response.text[:200]}..."); return None
    except requests.exceptions.ConnectionError as e: logging.error(f"[Alert Script] Errore Conn: {e}"); return None
    except requests.exceptions.RequestException as e: logging.error(f"[Alert Script] Errore Req: {e}"); return None
    except json.JSONDecodeError as e:
        resp_text=response.text[:200] if response else "N/A"; resp_status=response.status_code if response else "N/A"
        logging.error(f"[Alert Script] Errore JSON: Status {resp_status}, Resp '{resp_text}...', Err: {e}"); return None
    except Exception as e: logging.error(f"[Alert Script] Errore Imprevisto Fetch: {e}", exc_info=True); return None

def send_telegram_message(token, chat_id, text):
    if not token or not chat_id: logging.error("[Alert Script] Credenziali mancanti."); return False
    max_length=4096
    if len(text) > max_length:
        logging.warning(f"[Alert Script] Messaggio troppo lungo ({len(text)}), troncato.")
        text = text[:max_length-20] + "\n\n...[MESSAGGIO TRONCATO]..."
    url=f"https://api.telegram.org/bot{token}/sendMessage"
    payload={'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
    try:
        response=requests.post(url, data=payload, timeout=20)
        response.raise_for_status(); logging.info(f"[Alert Script] Msg inviato a {chat_id}"); return True
    except requests.exceptions.RequestException as e:
        logging.error(f"[Alert Script] Errore invio TG: {e}")
        if e.response is not None: logging.error(f"[Alert Script] Risposta API TG: {e.response.status_code} - {e.response.text}")
        return False

# --- Funzioni Helper per estrazione nomi stazione (Copiate) ---
def get_station_name_from_alert_string(alert_string):
    """Estrae il nome stazione da stringhe tipo '... Stazione: *Nome Stazione*\n ...'"""
    try:
        start_marker = "Stazione: *"
        start_index = alert_string.find(start_marker)
        if start_index == -1: return None
        start_index += len(start_marker)
        end_index = alert_string.find("*", start_index)
        if end_index == -1: return None
        return alert_string[start_index:end_index]
    except Exception as e:
        logging.warning(f"[Alert Script] Impossibile estrarre nome staz da alert: {alert_string[:50]}.. Err: {e}")
        return None

# --- Funzione di ordinamento personalizzata (Copiata) ---
def sort_key_station_order(item_string, bacino_name, get_name_func):
    """Genera chiave ordinamento basata su ORDINE_STAZIONI_PER_BACINO."""
    station_name = get_name_func(item_string)
    if station_name is None: return (float('inf'), "")
    order_list = ORDINE_STAZIONI_PER_BACINO.get(bacino_name)
    if order_list and station_name in order_list:
        return (order_list.index(station_name), station_name)
    else:
        return (float('inf'), station_name) # Non trovato o bacino non in ordine -> fine, alfabetico

# --- Logica Principale Solo Alert (Modificata per Bacini, Trend, Ordinamento) ---

def check_stazioni_alert():
    """
    Controlla i dati delle stazioni, raggruppa gli alert per bacino
    e restituisce un dizionario di alert e un eventuale errore fetch.
    """
    soglie_per_bacino = defaultdict(list) # Dizionario per raggruppare alert per bacino
    errore_fetch = None

    logging.info(f"[Alert Script] Controllo dati stazioni da {URL_STAZIONI}...")
    data = fetch_data(URL_STAZIONI)
    if data is None:
        errore_fetch = "⚠️ Impossibile recuperare dati stazioni meteo."
        # Ritorna dizionario vuoto e errore
        return (soglie_per_bacino, errore_fetch)

    stazioni_trovate_interessanti = False
    for stazione in data:
        nome_stazione_raw = stazione.get("nome", "N/A"); nome_stazione = nome_stazione_raw.strip()
        codice_stazione = stazione.get("codice"); is_arcevia = "Arcevia" in nome_stazione_raw

        # Filtro stazioni (come nel primo script)
        if not ((is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO) or \
                (not is_arcevia and nome_stazione in STAZIONI_INTERESSATE)):
            continue

        # Correzione nome Arcevia per matching dizionari
        if is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO:
             nome_stazione = "Arcevia"

        stazioni_trovate_interessanti = True
        # Determina bacino
        nome_bacino = BACINI_STAZIONI.get(nome_stazione, "Altri Bacini")

        sensori = stazione.get("analog", [])
        if not sensori:
            logging.debug(f"[Alert Script] Nessun sensore per {nome_stazione}")
            continue

        last_update = stazione.get("lastUpdateTime", "N/A")

        for sensore in sensori:
            tipoSens = sensore.get("tipoSens")

            # Determina soglia applicabile (specifica o generica)
            soglia_da_usare = None
            sorgente_soglia = "Nessuna"
            if nome_stazione in SOGLIE_PER_STAZIONE and tipoSens in SOGLIE_PER_STAZIONE[nome_stazione]:
                soglia_da_usare = SOGLIE_PER_STAZIONE[nome_stazione][tipoSens]
                sorgente_soglia = f"Specifica ({nome_stazione})"
            elif tipoSens in SOGLIE_GENERICHE:
                soglia_da_usare = SOGLIE_GENERICHE[tipoSens]
                sorgente_soglia = "Generica"

            # Processa SOLO se una soglia è definita per questo sensore/stazione
            if soglia_da_usare is not None:
                valore_str = sensore.get("valore")
                unmis = sensore.get("unmis", "").strip()
                descr_sens = sensore.get("descr", DESCRIZIONI_SENSORI.get(tipoSens, f"Sensore {tipoSens}")).strip()
                trend_symbol = "" # Inizializza simbolo trend

                try:
                    if valore_str is not None and valore_str != "" and valore_str.lower() != 'nan':
                        valore_num = float(valore_str)
                        valore_display = f"{valore_num:.2f} {unmis}" # Formatta valore

                        # --- Calcolo Trend (solo se idrometrico) ---
                        if tipoSens in SENSORI_IDROMETRICI_TREND:
                            trend_raw = sensore.get("trend")
                            if trend_raw is not None:
                                try:
                                    trend_num = float(trend_raw)
                                    if trend_num > 1e-9: trend_symbol = "📈"
                                    elif trend_num < -1e-9: trend_symbol = "📉"
                                    else: trend_symbol = "➡️"
                                except (ValueError, TypeError):
                                    logging.warning(f"[Alert Script] Err conv trend '{trend_raw}' sens {tipoSens} staz {nome_stazione}")
                                    trend_symbol = "❓"
                            else:
                                trend_symbol = "➡️" # Trend nullo = Stabile

                        # --- Controllo Superamento Soglia ---
                        if valore_num > soglia_da_usare:
                            # Aggiungi simbolo trend al display del valore nell'alert
                            trend_display_alert = f" {trend_symbol}" if trend_symbol else ""
                            # Crea messaggio di alert (usando ‼️)
                            msg = (f"‼️ *Soglia Superata!* ({sorgente_soglia})\n" # Uso ‼️ per coerenza
                                   f"   Stazione: *{nome_stazione}*\n" # Formato per estrazione nome
                                   f"   Sensore: {descr_sens}\n"
                                   f"   Valore: *{valore_display}{trend_display_alert}* (Soglia: {soglia_da_usare} {unmis})\n"
                                   f"   Ultimo Agg.: {last_update}")
                            # Aggiungi al dizionario del bacino corretto
                            soglie_per_bacino[nome_bacino].append(msg)
                            logging.warning(f"[Alert Script] SOGLIA SUPERATA ({sorgente_soglia}): Bacino {nome_bacino} - {nome_stazione} - {descr_sens} = {valore_num}{trend_display_alert} > {soglia_da_usare}")
                    else:
                        # Non è un errore se il valore è nullo/nan, ma non possiamo controllare la soglia
                         logging.debug(f"[Alert Script] Val non num o assente per sens {tipoSens} staz {nome_stazione}: '{valore_str}'")

                except (ValueError, TypeError) as e:
                    logging.warning(f"[Alert Script] Impossibile convertire val '{valore_str}' sens {tipoSens} staz {nome_stazione}: {e}")
            # else: Non fare nulla se non c'è una soglia definita per questo sensore

    if not stazioni_trovate_interessanti:
        logging.info(f"[Alert Script] Nessuna stazione di interesse trovata nei dati API.")
    # Non loggare "Nessuna soglia superata" qui, lo faremo nel main se necessario

    # Ritorna il dizionario (anche vuoto) e l'eventuale errore
    return (soglie_per_bacino, errore_fetch)

# --- Esecuzione Script Alert (Modificato per Formattazione Bacini/Ordinamento) ---
if __name__ == "__main__":
    logging.info("--- [Alert Script] Avvio Controllo SUPERAMENTO SOGLIE ---")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.critical("[Alert Script] Errore: Credenziali Telegram mancanti."); exit(1)

    # Chiama la funzione aggiornata
    dict_soglie_superate, errore_fetch = check_stazioni_alert()

    # Gestione errore fetch PRIMA di controllare le soglie
    if errore_fetch:
        messaggio_errore = f"*{'='*5} Errore Controllo Stazioni ({datetime.now().strftime('%d/%m/%Y %H:%M:%S')}) {'='*5}*\n\n{errore_fetch}"
        logging.error(f"[Alert Script] Invio messaggio di errore fetch: {errore_fetch}")
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_errore)

    # Controlla se ci sono soglie superate (verificando se il dizionario ha contenuti)
    elif any(dict_soglie_superate.values()):
        messaggio_finale_parts = []
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        header = f"*{'='*5} Report SUPERAMENTO SOGLIE ({timestamp}) {'='*5}*"
        footer = f"\n\n*{'='*30}*"

        messaggio_finale_parts.append(header)
        messaggio_finale_parts.append("\n\n*--- ‼️ SOGLIE SUPERATE ‼️ ---*") # Intestazione generale

        # Itera sui bacini nell'ordine definito
        for bacino in ORDINE_BACINI:
            if dict_soglie_superate[bacino]: # Se ci sono alert per questo bacino
                messaggio_finale_parts.append(f"\n\n*- Bacino {bacino} -*") # Intestazione del bacino
                # Ordina i messaggi di alert per questo bacino usando la chiave personalizzata
                soglie_ordinate = sorted(
                    dict_soglie_superate[bacino],
                    key=lambda msg: sort_key_station_order(msg, bacino, get_station_name_from_alert_string)
                )
                messaggio_finale_parts.extend(soglie_ordinate) # Aggiunge gli alert ordinati

        messaggio_finale_parts.append(footer) # Aggiunge il footer
        messaggio_da_inviare = "\n".join(messaggio_finale_parts) # Unisce tutto

        logging.info("[Alert Script] Invio messaggio soglie superate a Telegram...")
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_da_inviare)
    else:
        # Se non c'è errore fetch e non ci sono soglie superate, logga soltanto
        logging.info("[Alert Script] Nessuna soglia superata da notificare.")

    logging.info("--- [Alert Script] Controllo SUPERAMENTO SOGLIE completato ---")
