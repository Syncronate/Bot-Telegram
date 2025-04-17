# -*- coding: utf-8 -*-
import requests
import os
import json
import logging
from datetime import datetime
import urllib3
from collections import defaultdict

# --- Configurazione Stazioni ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
URL_STAZIONI = "https://retemir.regione.marche.it/api/stations/rt-data"

# Mappa delle stazioni ai rispettivi bacini
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
    "Foce Cesano": "Cesano" # Nome usato nel codice precedente, corrisponde a "Fonte Cesano"? Verificare output API se necessario
}
# Lista stazioni di interesse (deriva direttamente dalla mappa dei bacini)
STAZIONI_INTERESSATE = list(BACINI_STAZIONI.keys())

# *** NUOVO: Definizione ordine stazioni per bacino ***
ORDINE_STAZIONI_PER_BACINO = {
    "Misa": ["Arcevia", "Serra dei Conti", "Barbara", "Pianello di Ostra", "Misa", "Senigallia", "Ponte Garibaldi"],
    "Nevola": ["Corinaldo", "Nevola", "Passo Ripe"],
    "Cesano": ["Cesano", "Foce Cesano"] # Aggiornato anche qui con "Foce Cesano"
    # Aggiungere altri bacini e il loro ordine se necessario
}
# Aggiungere un ordine per "Altri Bacini" se si vuole ordinamento alfabetico o altro
# ORDINE_STAZIONI_PER_BACINO["Altri Bacini"] = [] # Lascia vuoto per ordinamento alfabetico di default (vedi sotto)


CODICE_ARCEVIA_CORRETTO = 732
DESCRIZIONI_SENSORI = {
    0: "Pioggia TOT Oggi", 1: "Intensità Pioggia mm/min", 5: "Temperatura Aria",
    6: "Umidità Relativa", 8: "Pressione Atmosferica", 9: "Direzione Vento",
    10: "Velocità Vento", 100: "Livello Idrometrico", 101: "Livello Idrometrico 2",
    7: "Radiazione Globale", 107: "Livello Neve"
}
SOGLIE_GENERICHE = {  }
SOGLIE_PER_STAZIONE = {
    "Misa": { 100: 2.0 }, "Ponte Garibaldi": { 101: 2.2 },
    "Serra dei Conti": { 100: 1.7 }, "Nevola": { 100: 2.0 },
    "Passo Ripe": { 100: 1.2 }, "Cesano": { 100: 1.0 }, "Foce Cesano": { 100: 1.5 }
}
SENSORI_IDROMETRICI_TREND = [100, 101]
ORDINE_BACINI = ["Misa", "Nevola", "Cesano", "Altri Bacini"]

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
    if len(text) > max_length:
        logging.warning(f"[Full Report Script] Messaggio troppo lungo ({len(text)} caratteri), verrà troncato.")
        text = text[:max_length-20] + "\n\n...[MESSAGGIO TRONCATO]..."

    url=f"https://api.telegram.org/bot{token}/sendMessage"
    payload={'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
    try:
        response=requests.post(url, data=payload, timeout=20)
        response.raise_for_status(); logging.info(f"[Full Report Script] Msg inviato a {chat_id}"); return True
    except requests.exceptions.RequestException as e:
        logging.error(f"[Full Report Script] Errore invio TG: {e}")
        if e.response is not None: logging.error(f"[Full Report Script] Risposta API TG: {e.response.status_code} - {e.response.text}")
        return False


# --- Logica Principale Full Report (Invariata - l'ordinamento è nel main) ---
def check_stazioni_full_report():
    """
    Controlla stazioni, raggruppa i dati per bacino e restituisce tuple di dizionari:
    (soglie_superate_per_bacino, valori_attuali_per_bacino, errore_fetch).
    L'ordinamento delle stazioni all'interno dei bacini viene fatto dopo.
    """
    soglie_per_bacino = defaultdict(list)
    valori_per_bacino = defaultdict(list)
    errore_fetch = None

    logging.info(f"[Full Report Script] Controllo dati stazioni da {URL_STAZIONI}...")
    data = fetch_data(URL_STAZIONI)
    if data is None:
        errore_fetch = "⚠️ Impossibile recuperare dati stazioni meteo."
        return (soglie_per_bacino, valori_per_bacino, errore_fetch)

    stazioni_trovate_interessanti = False
    for stazione in data:
        nome_stazione_raw = stazione.get("nome", "N/A"); nome_stazione = nome_stazione_raw.strip()
        codice_stazione = stazione.get("codice"); is_arcevia = "Arcevia" in nome_stazione_raw

        if not ((is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO) or \
                (not is_arcevia and nome_stazione in STAZIONI_INTERESSATE)):
            continue

        if is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO:
             nome_stazione = "Arcevia" # Nome pulito per matching

        stazioni_trovate_interessanti = True
        nome_bacino = BACINI_STAZIONI.get(nome_stazione, "Altri Bacini")
        sensori = stazione.get("analog", []); last_update = stazione.get("lastUpdateTime", "N/A")
        valori_stazione_str_list = []
        ha_valori_monitorati = False

        if not sensori: continue

        for sensore in sensori:
            tipoSens = sensore.get("tipoSens")
            soglia_da_usare = None; sorgente_soglia = "Nessuna"

            if nome_stazione in SOGLIE_PER_STAZIONE and tipoSens in SOGLIE_PER_STAZIONE[nome_stazione]:
                soglia_da_usare = SOGLIE_PER_STAZIONE[nome_stazione][tipoSens]; sorgente_soglia = f"Specifica ({nome_stazione})"
            elif tipoSens in SOGLIE_GENERICHE:
                 soglia_da_usare = SOGLIE_GENERICHE[tipoSens]; sorgente_soglia = "Generica"

            if soglia_da_usare is not None:
                 valore_str = sensore.get("valore"); descr_sens = sensore.get("descr", DESCRIZIONI_SENSORI.get(tipoSens, f"Sensore {tipoSens}")).strip()
                 unmis = sensore.get("unmis", "").strip(); valore_display = "N/D"
                 trend_symbol = ""

                 try:
                     if valore_str is not None and valore_str != "" and valore_str.lower() != 'nan':
                         valore_num = float(valore_str); valore_display = f"{valore_num:.2f} {unmis}"

                         if tipoSens in SENSORI_IDROMETRICI_TREND:
                             trend_raw = sensore.get("trend")
                             if trend_raw is not None:
                                 try:
                                     trend_num = float(trend_raw)
                                     if trend_num > 1e-9: trend_symbol = "📈"
                                     elif trend_num < -1e-9: trend_symbol = "📉"
                                     else: trend_symbol = "➡️"
                                 except (ValueError, TypeError):
                                     logging.warning(f"[Full Report Script] Err conversione trend '{trend_raw}' sensore {tipoSens} staz {nome_stazione}")
                                     trend_symbol = "❓"
                             else:
                                 trend_symbol = "➡️"

                         if valore_num > soglia_da_usare:
                             trend_display_soglia = f" {trend_symbol}" if trend_symbol else ""
                             # *** NOTA: Assicurarsi che il formato del msg soglia permetta facile estrazione del nome stazione ***
                             msg_soglia = (f"‼️ *Soglia Superata!* ({sorgente_soglia})\n"
                                           f"   Stazione: *{nome_stazione}*\n" # <<< Formato chiave per l'estrazione
                                           f"   Sensore: {descr_sens}\n"
                                           f"   Valore: *{valore_display}{trend_display_soglia}* (Soglia: {soglia_da_usare} {unmis})\n"
                                           f"   Ultimo Agg.: {last_update}")
                             soglie_per_bacino[nome_bacino].append(msg_soglia)
                             logging.warning(f"[Full Report Script] SOGLIA SUPERATA ({sorgente_soglia}): Bacino {nome_bacino} - {nome_stazione} - {descr_sens} = {valore_num}{trend_display_soglia} > {soglia_da_usare}")

                     trend_display_valore = f" {trend_symbol}" if trend_symbol else ""
                     valori_stazione_str_list.append(f"  - {descr_sens}: *{valore_display}{trend_display_valore}* (Soglia: {soglia_da_usare} {unmis})")
                     ha_valori_monitorati = True

                 except (ValueError, TypeError) as e:
                     logging.warning(f"[Full Report Script] Err conversione valore '{valore_str}' sensore {tipoSens} staz {nome_stazione}: {e}")
                     valori_stazione_str_list.append(f"  - {descr_sens}: *{valore_str}* (Val non num, Soglia: {soglia_da_usare} {unmis})")
                     ha_valori_monitorati = True

        if ha_valori_monitorati:
            # *** NOTA: Assicurarsi che il formato permetta facile estrazione del nome stazione ***
            header_stazione = f"*{nome_stazione}* (Agg: {last_update}):" # <<< Formato chiave per l'estrazione
            stringa_completa_stazione = header_stazione + "\n" + "\n".join(valori_stazione_str_list)
            valori_per_bacino[nome_bacino].append(stringa_completa_stazione)

    if not stazioni_trovate_interessanti:
        logging.info(f"[Full Report Script] Nessuna stazione di interesse trovata tra quelle attive.")

    return (soglie_per_bacino, valori_per_bacino, errore_fetch)


# --- Funzioni Helper per estrazione nomi stazione ---
def get_station_name_from_value_string(value_string):
    """Estrae il nome stazione da stringhe tipo '*NomeStazione* (Agg: ...):'"""
    try:
        return value_string.split('*')[1]
    except IndexError:
        logging.warning(f"Impossibile estrarre nome stazione da valore: {value_string[:50]}...")
        return None # Ritorna None se non riesce

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
        logging.warning(f"Impossibile estrarre nome stazione da alert: {alert_string[:50]}... Errore: {e}")
        return None # Ritorna None se non riesce

# --- Funzione di ordinamento personalizzata ---
def sort_key_station_order(item_string, bacino_name, get_name_func):
    """
    Genera una chiave di ordinamento basata sull'ordine definito in ORDINE_STAZIONI_PER_BACINO.
    Mette gli elementi non trovati nell'ordine definito alla fine, ordinati alfabeticamente.
    """
    station_name = get_name_func(item_string)
    if station_name is None:
        return (float('inf'), "") # Metti gli elementi non parsabili alla fine

    order_list = ORDINE_STAZIONI_PER_BACINO.get(bacino_name)

    if order_list and station_name in order_list:
        return (order_list.index(station_name), station_name) # Chiave: (indice, nome)
    else:
        # Stazione non nell'ordine definito o bacino non definito
        # Mettila dopo quelle ordinate, in ordine alfabetico
        return (float('inf'), station_name)


# --- Esecuzione Script Full Report (Modificata per Ordinamento Stazioni) ---
if __name__ == "__main__":
    logging.info("--- [Full Report Script] Avvio Controllo Stazioni ---")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.critical("[Full Report Script] Errore: Credenziali Telegram mancanti."); exit(1)

    dict_soglie_superate, dict_valori_attuali, errore_fetch = check_stazioni_full_report()
    messaggio_finale_parts = []
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    header_base = f"*{'='*5} Report Stazioni ({timestamp}) {'='*5}*"
    footer = f"\n\n*{'='*30}*"

    messaggio_finale_parts.append(header_base)

    if errore_fetch:
        messaggio_finale_parts.append(f"\n\n{errore_fetch}")
        logging.error(f"[Full Report Script] Invio errore fetch: {errore_fetch}")
    else:
        ha_soglie_superate = any(dict_soglie_superate.values())
        ha_valori_attuali = any(dict_valori_attuali.values())

        # 1. Sezione Soglie Superate (raggruppata per bacino e ORDINATA per stazione)
        if ha_soglie_superate:
            messaggio_finale_parts.append("\n\n*--- ‼️ SOGLIE SUPERATE ‼️ ---*")
            for bacino in ORDINE_BACINI:
                if dict_soglie_superate[bacino]:
                    messaggio_finale_parts.append(f"\n\n*- Bacino {bacino} -*")
                    # *** NUOVO: Ordina i messaggi di soglia per questo bacino ***
                    soglie_ordinate = sorted(
                        dict_soglie_superate[bacino],
                        key=lambda msg: sort_key_station_order(msg, bacino, get_station_name_from_alert_string)
                    )
                    messaggio_finale_parts.extend(soglie_ordinate)
            messaggio_finale_parts.append(" ")

        # 2. Sezione Valori Attuali Monitorati (raggruppata per bacino e ORDINATA per stazione)
        if ha_valori_attuali:
            messaggio_finale_parts.append("\n\n*--- VALORI ATTUALI MONITORATI ---*")
            for bacino in ORDINE_BACINI:
                if dict_valori_attuali[bacino]:
                    messaggio_finale_parts.append(f"\n\n*- Bacino {bacino} -*")
                     # *** NUOVO: Ordina le stringhe dei valori attuali per questo bacino ***
                    valori_ordinati = sorted(
                        dict_valori_attuali[bacino],
                        key=lambda val_str: sort_key_station_order(val_str, bacino, get_station_name_from_value_string)
                    )
                    messaggio_finale_parts.extend(valori_ordinati)
        elif not ha_soglie_superate:
             messaggio_finale_parts.append("\n\n✅ Nessuna soglia superata e nessun dato monitorato rilevante al momento.")

        logging.info("[Full Report Script] Report completo preparato.")

    messaggio_finale_parts.append(footer)
    messaggio_finale = "\n".join(messaggio_finale_parts)

    if messaggio_finale.strip() != (header_base + footer).strip() and not errore_fetch:
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_finale)
    elif errore_fetch:
         send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_finale)
    else:
        logging.warning("[Full Report Script] Nessun messaggio significativo da inviare.")

    logging.info("--- [Full Report Script] Controllo Stazioni completato ---")
