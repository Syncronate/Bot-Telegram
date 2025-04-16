import requests
import os
import json
import logging
from datetime import datetime
import certifi # <-- AGGIUNGI QUESTO IMPORT

# --- Configurazione ---
# Recupera le credenziali dai secrets di GitHub Actions
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# URL API
URL_ALLERTA_DOMANI = "https://allertameteo.regione.marche.it/o/api/allerta/get-stato-allerta-domani"
URL_ALLERTA_OGGI = "https://allertameteo.regione.marche.it/o/api/allerta/get-stato-allerta"
URL_STAZIONI = "https://retemir.regione.marche.it/api/stations/rt-data"

# Filtri Allerete
AREE_INTERESSATE_ALLERTE = ["2", "4"]
LIVELLI_ALLERTA_IGNORATI = ["green", "white"]

# Filtri Stazioni Meteo
STAZIONI_INTERESSATE = [
    "Misa", "Pianello di Ostra", "Nevola", "Barbara",
    "Serra dei Conti", "Arcevia", "Corinaldo", "Ponte Garibaldi",
]
# Assicurati che il codice per Arcevia sia quello corretto se ci sono duplicati
CODICE_ARCEVIA_CORRETTO = 732

# Tipi Sensore di interesse (Assumiamo 0 = Pioggia TOT Oggi basato sull'esempio)
# Verifica se il tipoSens 5 (Temperatura) o altri sono necessari per le soglie
SENSORI_INTERESSATI_TIPOSENS = [0, 1, 5, 6, 9, 10, 100, 101]

# Mappa descrizioni leggibili per tipoSens (Opzionale ma utile per i messaggi)
DESCRIZIONI_SENSORI = {
    0: "Pioggia TOT Oggi",
    1: "Intensità Pioggia",
    5: "Temperatura Aria",
    6: "Umidità Relativa",
    8: "Pressione Atmosferica",
    9: "Direzione Vento",
    10: "Velocità Vento",
    100: "Livello Idrometrico", # Potrebbe essere specifico del fiume
    101: "Livello Idrometrico 2", # Potrebbe essere specifico del fiume
    7: "Radiazione Globale",
    107: "Livello Neve"
}

# Soglie per i sensori (tipoSens -> valore soglia)
# Esempio: Notifica se Pioggia TOT Oggi (0) > 50mm o Temperatura (5) > 35°C
SOGLIE_SENSORI = {
    0: 50.0,   # Pioggia TOT Oggi in mm
    5: 35.0,   # Temperatura in °C
    10: 15.0,  # Velocità Vento in m/s (circa 54 km/h)
    100: 3.0,  # Livello Idrometrico in metri (ESEMPIO - da adattare!)
    101: 2.0   # Livello Idrometrico 2 in metri (ESEMPIO - da adattare!)
}

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Funzioni Helper ---

def fetch_data(url):
    """Recupera dati JSON da un URL con User-Agent, certifi e logging migliorato."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        # Aggiungi verify=certifi.where()
        response = requests.get(url, headers=headers, timeout=45, verify=certifi.where()) # <-- MODIFICA QUI
        logging.info(f"Richiesta a {url} - Status Code: {response.status_code}")
        response.raise_for_status()
        return response.json()
    # ... (il resto della gestione errori rimane uguale) ...
    except requests.exceptions.Timeout as e:
        logging.error(f"Timeout durante la richiesta a {url}: {e}")
        return None
    except requests.exceptions.HTTPError as e:
        logging.error(f"Errore HTTP durante la richiesta a {url}: {e.response.status_code} - {e.response.text[:200]}...")
        return None
    except requests.exceptions.ConnectionError as e:
         # Controlla se l'errore è ancora SSL
         if isinstance(e.args[0], javax.net.ssl.SSLError) or 'CERTIFICATE_VERIFY_FAILED' in str(e): # Adattato per possibile struttura errore requests/urllib3
            logging.error(f"Errore SSL persistente con certifi durante richiesta a {url}: {e}")
         else:
            logging.error(f"Errore di connessione generico durante richiesta a {url}: {e}")
         return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Errore generico durante la richiesta a {url}: {e}")
        return None
    except json.JSONDecodeError as e:
        # Assicurati che la response esista prima di accedere a text
        resp_text = response.text[:200] if response else "N/A"
        resp_status = response.status_code if response else "N/A"
        logging.error(f"Errore nel decodificare JSON da {url}. Status: {resp_status}. Risposta (primi 200 char): '{resp_text}...' Errore: {e}")
        return None
    except Exception as e:
        logging.error(f"Errore imprevisto durante il fetch da {url}: {e}", exc_info=True)
        return None

def send_telegram_message(token, chat_id, text):
    """Invia un messaggio a una chat Telegram."""
    if not token or not chat_id:
        logging.error("Token Telegram o Chat ID non configurati.")
        return False
    # Limita la lunghezza del messaggio per evitare errori API Telegram
    max_length = 4096
    if len(text) > max_length:
        logging.warning(f"Messaggio troppo lungo ({len(text)} caratteri), troncato a {max_length}.")
        text = text[:max_length - 3] + "..." # Tronca e aggiunge puntini

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'Markdown' # Opzionale: per usare formattazione come *bold* o _italic_
    }
    try:
        response = requests.post(url, data=payload, timeout=15)
        response.raise_for_status()
        logging.info(f"Messaggio inviato con successo a chat ID {chat_id}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Errore durante l'invio del messaggio Telegram: {e}")
        # Logga anche la risposta se disponibile per debug
        if e.response is not None:
            logging.error(f"Risposta API Telegram: {e.response.status_code} - {e.response.text}")
        return False

def formatta_evento_allerta(evento_str):
    """Formatta la stringa evento:colore in modo leggibile."""
    try:
        nome, colore = evento_str.split(':')
        # Mappa colori a emoji o testo (opzionale)
        emoji_map = {
            "yellow": "🟡",
            "orange": "🟠",
            "red": "🔴",
            # "green": "🟢", # Ignorati
            # "white": "⚪️" # Ignorati
        }
        # Sostituisci underscore con spazi e capitalizza
        nome_formattato = nome.replace("_", " ").capitalize()
        if colore in emoji_map:
            return f"{emoji_map[colore]} {nome_formattato} ({colore})"
        elif colore not in LIVELLI_ALLERTA_IGNORATI:
             return f"❓ {nome_formattato} ({colore})" # Colore sconosciuto ma non ignorato
        else:
            return None # Ignora green/white
    except ValueError:
        return f"Evento malformato: {evento_str}" # Gestisce casi imprevisti

# --- Logica Principale ---

def check_allerte():
    """Controlla le API di allerta e restituisce un messaggio se ci sono allerte rilevanti."""
    messaggi_allerta = []
    urls_allerte = {
        "OGGI": URL_ALLERTA_OGGI,
        "DOMANI": URL_ALLERTA_DOMANI
    }

    for tipo_giorno, url in urls_allerte.items():
        logging.info(f"Controllo allerte {tipo_giorno} da {url}...")
        data = fetch_data(url)
        if not data:
            messaggi_allerta.append(f"⚠️ Impossibile recuperare dati allerta {tipo_giorno}.")
            continue

        allerte_rilevanti_giorno = []
        for item in data:
            area = item.get("area")
            eventi_str = item.get("eventi")

            if area in AREE_INTERESSATE_ALLERTE and eventi_str:
                eventi_list = eventi_str.split(',')
                eventi_formattati_area = []
                for evento in eventi_list:
                    evento_formattato = formatta_evento_allerta(evento.strip())
                    if evento_formattato:
                        eventi_formattati_area.append(evento_formattato)

                if eventi_formattati_area: # Se ci sono eventi NON green/white per quest'area
                     allerte_rilevanti_giorno.append(f"  - *Area {area}*:\n    " + "\n    ".join(eventi_formattati_area))

        if allerte_rilevanti_giorno:
             messaggi_allerta.append(f"🚨 *Allerte Meteo {tipo_giorno}:*\n" + "\n".join(allerte_rilevanti_giorno))
        else:
             logging.info(f"Nessuna allerta meteo rilevante trovata per {tipo_giorno} nelle aree {AREE_INTERESSATE_ALLERTE}.")

    return "\n\n".join(messaggi_allerta) if messaggi_allerta else ""

def check_stazioni():
    """Controlla i dati delle stazioni meteo e verifica le soglie."""
    messaggi_soglia = []
    logging.info(f"Controllo dati stazioni da {URL_STAZIONI}...")
    data = fetch_data(URL_STAZIONI)
    if not data:
        return "⚠️ Impossibile recuperare dati stazioni meteo."

    stazioni_trovate_interessanti = False
    for stazione in data:
        nome_stazione = stazione.get("nome", "N/A").strip()
        codice_stazione = stazione.get("codice")

        # Gestione caso speciale Arcevia e filtro per nome
        is_arcevia = "Arcevia" in nome_stazione
        if (is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO) or \
           (not is_arcevia and nome_stazione in STAZIONI_INTERESSATE):

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

                    # Controllo Soglie
                    if tipoSens in SOGLIE_SENSORI:
                        soglia = SOGLIE_SENSORI[tipoSens]
                        try:
                            # Gestisce valori non numerici o nulli
                            if valore_str is not None and valore_str != "" and valore_str.lower() != 'nan':
                                valore_num = float(valore_str)
                                if valore_num > soglia:
                                    msg = (f"📈 *Soglia Superata!*\n"
                                           f"   Stazione: *{nome_stazione}*\n"
                                           f"   Sensore: {descr_sens}\n"
                                           f"   Valore: *{valore_num} {unmis}* (Soglia: {soglia} {unmis})\n"
                                           f"   Ultimo Agg.: {last_update}")
                                    messaggi_soglia.append(msg)
                                    logging.warning(f"SOGLIA SUPERATA: {nome_stazione} - {descr_sens} = {valore_num} > {soglia}")
                            else:
                                logging.debug(f"Valore non numerico o assente per sensore {tipoSens} in stazione {nome_stazione}: '{valore_str}'")
                        except (ValueError, TypeError) as e:
                            logging.warning(f"Impossibile convertire valore '{valore_str}' a float per sensore {tipoSens} in stazione {nome_stazione}: {e}")

    if not stazioni_trovate_interessanti:
        logging.info(f"Nessuna delle stazioni di interesse ({', '.join(STAZIONI_INTERESSATE)}) trovata nei dati API.")
    elif not messaggi_soglia:
         logging.info("Nessuna soglia superata per le stazioni monitorate.")


    return "\n\n".join(messaggi_soglia) if messaggi_soglia else ""


# --- Esecuzione ---
if __name__ == "__main__":
    logging.info("Avvio controllo Meteo Marche...")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.critical("Errore: Le variabili d'ambiente TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID sono necessarie.")
        exit(1) # Esce con errore

    # Controlla Allerete
    messaggio_finale_allerte = check_allerte()

    # Controlla Stazioni e Soglie
    messaggio_finale_soglie = check_stazioni()

    # Combina i messaggi se ci sono contenuti
    messaggio_completo = ""
    if messaggio_finale_allerte:
        messaggio_completo += messaggio_finale_allerte + "\n\n" # Aggiunge separatore
    if messaggio_finale_soglie:
        messaggio_completo += messaggio_finale_soglie

    # Invia il messaggio a Telegram solo se c'è qualcosa da riportare
    if messaggio_completo:
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        header = f"*{'='*10} Report Meteo Marche ({timestamp}) {'='*10}*\n\n"
        footer = f"\n\n*{'='*30}*"
        messaggio_da_inviare = header + messaggio_completo.strip() + footer

        logging.info("Invio messaggio aggregato a Telegram...")
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_da_inviare)
    else:
        logging.info("Nessuna allerta meteo rilevante o soglia superata da notificare.")

    logging.info("Controllo Meteo Marche completato.")
