# -*- coding: utf-8 -*-
import requests
import os
import json
import logging
from datetime import datetime
import urllib3
from collections import defaultdict # Importato per semplificare la gestione dei dizionari

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
    "Misa": "Misa", # Assumo che la stazione "Misa" sia nel bacino Misa
    "Senigallia": "Misa",
    "Ponte Garibaldi": "Misa",
    # Bacino Nevola
    "Corinaldo": "Nevola",
    "Nevola": "Nevola", # Assumo che la stazione "Nevola" sia nel bacino Nevola
    "Passo Ripe": "Nevola",
    # Bacino Cesano
    "Cesano": "Cesano", # Assumo che la stazione "Cesano" sia nel bacino Cesano
    "Foce Cesano": "Cesano" # Modificato da "Fonte Cesano" come nell'elenco, ma il codice cercava "Foce Cesano"
}
# Lista stazioni di interesse (usata per il filtro iniziale)
STAZIONI_INTERESSATE = list(BACINI_STAZIONI.keys()) # Deriva direttamente dalla mappa dei bacini

CODICE_ARCEVIA_CORRETTO = 732
DESCRIZIONI_SENSORI = {
    0: "Pioggia TOT Oggi", 1: "Intensità Pioggia mm/min", 5: "Temperatura Aria",
    6: "Umidità Relativa", 8: "Pressione Atmosferica", 9: "Direzione Vento",
    10: "Velocità Vento", 100: "Livello Idrometrico", 101: "Livello Idrometrico 2",
    7: "Radiazione Globale", 107: "Livello Neve"
}
SOGLIE_GENERICHE = { 0: 15.0, 1: 0.25 }
SOGLIE_PER_STAZIONE = {
    # Misa
    "Misa": { 100: 2.0 },
    "Ponte Garibaldi": { 101: 2.2 },
    "Serra dei Conti": { 100: 1.7 },
    # Nevola
    "Nevola": { 100: 2.0, 1: 0.25 },
    "Passo Ripe": { 100: 1.2 },
    # Aggiungere qui altre soglie specifiche se necessario
}
# Sensori idrometrici per cui vogliamo il trend
SENSORI_IDROMETRICI_TREND = [100, 101]
# Ordine desiderato per la visualizzazione dei bacini nel messaggio
ORDINE_BACINI = ["Misa", "Nevola", "Cesano", "Altri Bacini"] # Aggiunto "Altri Bacini" per stazioni non mappate

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
    # Gestisce messaggi lunghi dividendoli se necessario (più robusto)
    if len(text) > max_length:
        logging.warning(f"[Full Report Script] Messaggio troppo lungo ({len(text)} caratteri), verrà troncato o inviato in parti.")
        # Semplice troncamento per ora, ma si potrebbe implementare l'invio multiplo
        text = text[:max_length-20] + "\n\n...[MESSAGGIO TRONCATO]..."

    url=f"https://api.telegram.org/bot{token}/sendMessage"
    payload={'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
    try:
        response=requests.post(url, data=payload, timeout=20) # Timeout aumentato leggermente
        response.raise_for_status(); logging.info(f"[Full Report Script] Msg inviato a {chat_id}"); return True
    except requests.exceptions.RequestException as e:
        logging.error(f"[Full Report Script] Errore invio TG: {e}")
        if e.response is not None: logging.error(f"[Full Report Script] Risposta API TG: {e.response.status_code} - {e.response.text}")
        return False

# --- Logica Principale Full Report (Modificata per Bacini) ---
def check_stazioni_full_report():
    """
    Controlla stazioni, raggruppa i dati per bacino e restituisce tuple di dizionari:
    (soglie_superate_per_bacino, valori_attuali_per_bacino, errore_fetch).
    """
    # Usiamo defaultdict per semplificare l'aggiunta di elementi a liste non ancora esistenti
    soglie_per_bacino = defaultdict(list)
    valori_per_bacino = defaultdict(list)
    errore_fetch = None

    logging.info(f"[Full Report Script] Controllo dati stazioni da {URL_STAZIONI}...")
    data = fetch_data(URL_STAZIONI)
    if data is None:
        errore_fetch = "⚠️ Impossibile recuperare dati stazioni meteo."
        return (soglie_per_bacino, valori_per_bacino, errore_fetch) # Ritorna dizionari vuoti e l'errore

    stazioni_trovate_interessanti = False
    for stazione in data:
        # Filtro stazioni
        nome_stazione_raw = stazione.get("nome", "N/A"); nome_stazione = nome_stazione_raw.strip()
        codice_stazione = stazione.get("codice"); is_arcevia = "Arcevia" in nome_stazione_raw

        # Applica filtro per Arcevia specifica o altre stazioni di interesse
        if not ((is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO) or \
                (not is_arcevia and nome_stazione in STAZIONI_INTERESSATE)):
            continue

        # Correzione nome Arcevia se necessario (per matching con BACINI_STAZIONI)
        if is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO:
             nome_stazione = "Arcevia" # Usa il nome pulito per la ricerca nel dizionario bacini

        stazioni_trovate_interessanti = True

        # Determina il bacino di appartenenza
        nome_bacino = BACINI_STAZIONI.get(nome_stazione, "Altri Bacini") # Default a "Altri Bacini" se non mappata

        sensori = stazione.get("analog", []); last_update = stazione.get("lastUpdateTime", "N/A")
        valori_stazione_str_list = [] # Lista temporanea per i valori di questa stazione
        ha_valori_monitorati = False

        if not sensori:
            logging.debug(f"[Full Report Script] Nessun sensore per {nome_stazione}");
            continue

        for sensore in sensori:
            tipoSens = sensore.get("tipoSens")
            soglia_da_usare = None; sorgente_soglia = "Nessuna"

            # Determina soglia applicabile
            if nome_stazione in SOGLIE_PER_STAZIONE and tipoSens in SOGLIE_PER_STAZIONE[nome_stazione]:
                soglia_da_usare = SOGLIE_PER_STAZIONE[nome_stazione][tipoSens]; sorgente_soglia = f"Specifica ({nome_stazione})"
            elif tipoSens in SOGLIE_GENERICHE:
                 soglia_da_usare = SOGLIE_GENERICHE[tipoSens]; sorgente_soglia = "Generica"

            # Processa solo i sensori per cui abbiamo definito una soglia
            if soglia_da_usare is not None:
                 valore_str = sensore.get("valore"); descr_sens = sensore.get("descr", DESCRIZIONI_SENSORI.get(tipoSens, f"Sensore {tipoSens}")).strip()
                 unmis = sensore.get("unmis", "").strip(); valore_display = "N/D"
                 trend_symbol = ""

                 try:
                     if valore_str is not None and valore_str != "" and valore_str.lower() != 'nan':
                         valore_num = float(valore_str); valore_display = f"{valore_num:.2f} {unmis}" # Formatta a 2 decimali

                         # Logica Trend (invariata)
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

                         # Controllo soglia superata
                         if valore_num > soglia_da_usare:
                             trend_display_soglia = f" {trend_symbol}" if trend_symbol else ""
                             msg_soglia = (f"‼️ *Soglia Superata!* ({sorgente_soglia})\n"
                                           f"   Stazione: *{nome_stazione}*\n"
                                           f"   Sensore: {descr_sens}\n"
                                           f"   Valore: *{valore_display}{trend_display_soglia}* (Soglia: {soglia_da_usare} {unmis})\n"
                                           f"   Ultimo Agg.: {last_update}")
                             # Aggiunge al dizionario del bacino corretto
                             soglie_per_bacino[nome_bacino].append(msg_soglia)
                             logging.warning(f"[Full Report Script] SOGLIA SUPERATA ({sorgente_soglia}): Bacino {nome_bacino} - {nome_stazione} - {descr_sens} = {valore_num}{trend_display_soglia} > {soglia_da_usare}")

                     # Aggiungi valore corrente (e trend) alla lista temporanea per questa stazione
                     trend_display_valore = f" {trend_symbol}" if trend_symbol else ""
                     # Mostra la soglia per riferimento anche nei valori attuali
                     valori_stazione_str_list.append(f"  - {descr_sens}: *{valore_display}{trend_display_valore}* (Soglia: {soglia_da_usare} {unmis})")
                     ha_valori_monitorati = True

                 except (ValueError, TypeError) as e:
                     logging.warning(f"[Full Report Script] Err conversione valore '{valore_str}' sensore {tipoSens} staz {nome_stazione}: {e}")
                     # Aggiunge comunque l'info che il valore non è numerico
                     valori_stazione_str_list.append(f"  - {descr_sens}: *{valore_str}* (Val non num, Soglia: {soglia_da_usare} {unmis})")
                     ha_valori_monitorati = True

        # Se la stazione ha avuto sensori monitorati, costruisci la stringa completa e aggiungila al dizionario del bacino
        if ha_valori_monitorati:
            header_stazione = f"*{nome_stazione}* (Agg: {last_update}):"
            stringa_completa_stazione = header_stazione + "\n" + "\n".join(valori_stazione_str_list)
            valori_per_bacino[nome_bacino].append(stringa_completa_stazione)

    if not stazioni_trovate_interessanti:
        logging.info(f"[Full Report Script] Nessuna stazione di interesse trovata tra quelle attive.")

    # Ritorna i dizionari (anche se vuoti) e l'eventuale errore
    return (soglie_per_bacino, valori_per_bacino, errore_fetch)

# --- Esecuzione Script Full Report (Modificata per Formattazione Bacini) ---
if __name__ == "__main__":
    logging.info("--- [Full Report Script] Avvio Controllo Stazioni ---")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.critical("[Full Report Script] Errore: Credenziali Telegram mancanti."); exit(1)

    dict_soglie_superate, dict_valori_attuali, errore_fetch = check_stazioni_full_report()
    messaggio_finale_parts = [] # Usiamo una lista per costruire il messaggio pezzo per pezzo
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    header_base = f"*{'='*5} Report Stazioni ({timestamp}) {'='*5}*"
    footer = f"\n\n*{'='*30}*"

    messaggio_finale_parts.append(header_base)

    if errore_fetch: # Caso Errore Fetch
        messaggio_finale_parts.append(f"\n\n{errore_fetch}")
        logging.error(f"[Full Report Script] Invio errore fetch: {errore_fetch}")
    else: # Caso Fetch OK
        ha_soglie_superate = any(dict_soglie_superate.values())
        ha_valori_attuali = any(dict_valori_attuali.values())

        # 1. Sezione Soglie Superate (raggruppata per bacino)
        if ha_soglie_superate:
            messaggio_finale_parts.append("\n\n*--- ‼️ SOGLIE SUPERATE ‼️ ---*")
            for bacino in ORDINE_BACINI:
                if dict_soglie_superate[bacino]: # Se ci sono soglie superate per questo bacino
                    messaggio_finale_parts.append(f"\n\n*- Bacino {bacino} -*") # Intestazione del bacino
                    messaggio_finale_parts.extend(dict_soglie_superate[bacino]) # Aggiunge tutti i messaggi di soglia per quel bacino
            messaggio_finale_parts.append(" ") # Spazio dopo la sezione soglie

        # 2. Sezione Valori Attuali Monitorati (raggruppata per bacino)
        if ha_valori_attuali:
            messaggio_finale_parts.append("\n\n*--- VALORI ATTUALI MONITORATI ---*")
            for bacino in ORDINE_BACINI:
                if dict_valori_attuali[bacino]: # Se ci sono valori attuali per questo bacino
                    messaggio_finale_parts.append(f"\n\n*- Bacino {bacino} -*") # Intestazione del bacino
                    # Aggiunge le stringhe formattate per ogni stazione del bacino
                    messaggio_finale_parts.extend(dict_valori_attuali[bacino])
        elif not ha_soglie_superate: # Se non c'è NIENTE da mostrare (né soglie, né valori)
             messaggio_finale_parts.append("\n\n✅ Nessuna soglia superata e nessun dato monitorato rilevante al momento.")

        logging.info("[Full Report Script] Report completo preparato.")

    # Aggiungi footer e unisci le parti
    messaggio_finale_parts.append(footer)
    messaggio_finale = "\n".join(messaggio_finale_parts)

    # Invia il messaggio finale (se non vuoto)
    if messaggio_finale.strip() != (header_base + footer).strip() and not errore_fetch: # Invia solo se c'è contenuto oltre header/footer o c'è un errore
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_finale)
    elif errore_fetch:
         send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_finale) # Invia comunque l'errore
    else:
        logging.warning("[Full Report Script] Nessun messaggio significativo da inviare (nessuna soglia superata o valore monitorato).")

    logging.info("--- [Full Report Script] Controllo Stazioni completato ---")
