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
    "Serra dei Conti", "Arcevia", "Corinaldo", "Ponte Garibaldi",
]
CODICE_ARCEVIA_CORRETTO = 732
SENSORI_INTERESSATI_TIPOSENS = [0, 1, 5, 6, 9, 10, 100, 101]

DESCRIZIONI_SENSORI = {
    0: "Pioggia TOT Oggi", 1: "Intensità Pioggia", 5: "Temperatura Aria",
    6: "Umidità Relativa", 8: "Pressione Atmosferica", 9: "Direzione Vento",
    10: "Velocità Vento", 100: "Livello Idrometrico", 101: "Livello Idrometrico 2",
    7: "Radiazione Globale", 107: "Livello Neve"
}

# Soglie Generiche (Default)
SOGLIE_GENERICHE = {
    0: 50.0, 5: 35.0, 10: 15.0,
}

# Soglie Specifiche per Stazione
SOGLIE_PER_STAZIONE = {
    "Nevola": { 100: 2.0, 0: 60.0 },
    "Misa": { 100: 3.5 },
    "Ponte Garibaldi": { 101: 1.5 },
    "Arcevia": { 5: 32.0, 100: 1.8 },
    "Serra dei Conti": { 100: 1.2 }
}

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Disabilita avvisi SSL per verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Funzioni Helper (Copiate) ---

def fetch_data(url):
    """Recupera dati JSON da un URL DISABILITANDO la verifica SSL."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    response = None
    try:
        logging.warning(f"Tentativo di richiesta STAZIONI a {url} con VERIFICA SSL DISABILITATA (verify=False).")
        response = requests.get(url, headers=headers, timeout=45, verify=False)
        logging.info(f"Richiesta STAZIONI a {url} - Status Code: {response.status_code}")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout as e:
        logging.error(f"Timeout durante la richiesta STAZIONI a {url}: {e}")
        return None
    except requests.exceptions.HTTPError as e:
        resp_text = e.response.text[:200] if e.response else "N/A"
        logging.error(f"Errore HTTP durante la richiesta STAZIONI a {url}: {e.response.status_code} - {resp_text}...")
        return None
    except requests.exceptions.ConnectionError as e:
         logging.error(f"Errore di connessione durante richiesta STAZIONI a {url}: {e}")
         return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Errore generico durante la richiesta STAZIONI a {url}: {e}")
        return None
    except json.JSONDecodeError as e:
        resp_text = response.text[:200] if response else "N/A"
        resp_status = response.status_code if response else "N/A"
        logging.error(f"Errore nel decodificare JSON STAZIONI da {url}. Status: {resp_status}. Risposta (primi 200 char): '{resp_text}...' Errore: {e}")
        return None
    except Exception as e:
        logging.error(f"Errore imprevisto durante il fetch STAZIONI da {url}: {e}", exc_info=True)
        return None

def send_telegram_message(token, chat_id, text):
    """Invia un messaggio a una chat Telegram."""
    if not token or not chat_id:
        logging.error("Token Telegram o Chat ID non configurati.")
        return False
    max_length = 4096
    if len(text) > max_length:
        logging.warning(f"Messaggio troppo lungo ({len(text)} caratteri), troncato a {max_length}.")
        text = text[:max_length - 3] + "..."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
    try:
        response = requests.post(url, data=payload, timeout=15)
        response.raise_for_status()
        logging.info(f"Messaggio inviato con successo a chat ID {chat_id}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Errore durante l'invio del messaggio Telegram: {e}")
        if e.response is not None: logging.error(f"Risposta API Telegram: {e.response.status_code} - {e.response.text}")
        return False

# --- Logica Principale Solo Stazioni ---

def check_stazioni_principale():
    """Controlla i dati delle stazioni meteo e verifica le soglie (specifiche e generiche)."""
    messaggi_soglia = []
    logging.info(f"Controllo dati stazioni da {URL_STAZIONI}...")
    data = fetch_data(URL_STAZIONI)
    if data is None:
        return "⚠️ Impossibile recuperare dati stazioni meteo."

    stazioni_trovate_interessanti = False
    for stazione in data:
        nome_stazione_raw = stazione.get("nome", "N/A")
        nome_stazione = nome_stazione_raw.strip()
        codice_stazione = stazione.get("codice")

        is_arcevia = "Arcevia" in nome_stazione_raw
        if not ((is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO) or \
                (not is_arcevia and nome_stazione in STAZIONI_INTERESSATE)):
            continue

        stazioni_trovate_interessanti = True
        logging.debug(f"Trovata stazione di interesse: {nome_stazione} (Codice: {codice_stazione})")
        sensori = stazione.get("analog", [])
        if not sensori:
            logging.debug(f"Nessun dato sensore per {nome_stazione}")
            continue

        last_update = stazione.get("lastUpdateTime", "N/A")

        for sensore in sensori:
            tipoSens = sensore.get("tipoSens")
            if tipoSens in SENSORI_INTERESSATI_TIPOSENS:
                valore_str = sensore.get("valore")
                unmis = sensore.get("unmis", "").strip()
                descr_sens = sensore.get("descr", DESCRIZIONI_SENSORI.get(tipoSens, f"Sensore {tipoSens}")).strip()
                logging.debug(f"  - Sensore: {descr_sens} ({tipoSens}), Valore: {valore_str} {unmis}, Aggiorn.: {last_update}")

                soglia_da_usare = None
                sorgente_soglia = "Nessuna"
                if nome_stazione in SOGLIE_PER_STAZIONE and tipoSens in SOGLIE_PER_STAZIONE[nome_stazione]:
                    soglia_da_usare = SOGLIE_PER_STAZIONE[nome_stazione][tipoSens]
                    sorgente_soglia = f"Specifica ({nome_stazione})"
                elif tipoSens in SOGLIE_GENERICHE:
                    soglia_da_usare = SOGLIE_GENERICHE[tipoSens]
                    sorgente_soglia = "Generica"

                if soglia_da_usare is not None:
                    logging.debug(f"Applicando soglia {soglia_da_usare} ({sorgente_soglia}) per sensore {tipoSens} a {nome_stazione}")
                    try:
                        if valore_str is not None and valore_str != "" and valore_str.lower() != 'nan':
                            valore_num = float(valore_str)
                            if valore_num > soglia_da_usare:
                                msg = (f"📈 *Soglia Superata!* ({sorgente_soglia})\n"
                                       f"   Stazione: *{nome_stazione}*\n"
                                       f"   Sensore: {descr_sens}\n"
                                       f"   Valore: *{valore_num} {unmis}* (Soglia: {soglia_da_usare} {unmis})\n"
                                       f"   Ultimo Agg.: {last_update}")
                                messaggi_soglia.append(msg)
                                logging.warning(f"SOGLIA SUPERATA ({sorgente_soglia}): {nome_stazione} - {descr_sens} = {valore_num} > {soglia_da_usare}")
                        else:
                            logging.debug(f"Valore non numerico o assente per sensore {tipoSens} in stazione {nome_stazione}: '{valore_str}'")
                    except (ValueError, TypeError) as e:
                        logging.warning(f"Impossibile convertire valore '{valore_str}' a float per sensore {tipoSens} in stazione {nome_stazione}: {e}")
                else:
                     logging.debug(f"Nessuna soglia specifica o generica trovata per sensore {tipoSens} a {nome_stazione}")

    if not stazioni_trovate_interessanti:
        logging.info(f"Nessuna delle stazioni di interesse ({', '.join(STAZIONI_INTERESSATE)}) trovata nei dati API.")
    elif not messaggi_soglia:
         logging.info("Nessuna soglia superata per le stazioni monitorate.")

    return "\n\n".join(messaggi_soglia) if messaggi_soglia else ""

# --- Esecuzione Script Stazioni ---
if __name__ == "__main__":
    logging.info("--- Avvio Controllo SOLO STAZIONI Meteo Marche ---")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.critical("Errore: Le variabili d'ambiente TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID sono necessarie.")
        exit(1)

    messaggio_soglie = check_stazioni_principale()

    if messaggio_soglie:
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        header = f"*{'='*5} Report SUPERAMENTO SOGLIE ({timestamp}) {'='*5}*\n\n"
        footer = f"\n\n*{'='*30}*"
        messaggio_da_inviare = header + messaggio_soglie.strip() + footer

        logging.info("Invio messaggio soglie superate a Telegram...")
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_da_inviare)
    else:
        logging.info("Nessuna soglia superata da notificare.")

    logging.info("--- Controllo SOLO STAZIONI Meteo Marche completato ---")
