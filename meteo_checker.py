import requests
import os
import json
import logging
from datetime import datetime
import urllib3 # Importa urllib3 per disabilitare gli avvisi

# --- Configurazione ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

URL_ALLERTA_DOMANI = "https://allertameteo.regione.marche.it/o/api/allerta/get-stato-allerta-domani"
URL_ALLERTA_OGGI = "https://allertameteo.regione.marche.it/o/api/allerta/get-stato-allerta"
URL_STAZIONI = "https://retemir.regione.marche.it/api/stations/rt-data"

AREE_INTERESSATE_ALLERTE = ["2", "4"]
LIVELLI_ALLERTA_IGNORATI = ["green", "white"]

STAZIONI_INTERESSATE = [
    "Misa", "Pianello di Ostra", "Nevola", "Barbara",
    "Serra dei Conti", "Arcevia", "Corinaldo", "Ponte Garibaldi",
]
CODICE_ARCEVIA_CORRETTO = 732 # Usato per disambiguare Arcevia
SENSORI_INTERESSATI_TIPOSENS = [0, 1, 5, 6, 9, 10, 100, 101]

DESCRIZIONI_SENSORI = {
    0: "Pioggia TOT Oggi", 1: "Intensità Pioggia", 5: "Temperatura Aria",
    6: "Umidità Relativa", 8: "Pressione Atmosferica", 9: "Direzione Vento",
    10: "Velocità Vento", 100: "Livello Idrometrico", 101: "Livello Idrometrico 2",
    7: "Radiazione Globale", 107: "Livello Neve"
}

# --- NUOVA CONFIGURAZIONE SOGLIE ---

# 1. Soglie Generiche (Default per tipo sensore, se non specificato diversamente)
SOGLIE_GENERICHE = {
    0: 5.0,   # Pioggia TOT Oggi in mm (default)
    1: 0.25
    5: 35.0,   # Temperatura in °C (default)
    10: 15.0,  # Velocità Vento in m/s (default)
    # NON mettiamo una soglia generica per 100/101, perché varia troppo
}

# 2. Soglie Specifiche per Stazione (Sovrascrivono quelle generiche)
#    Formato: { "Nome Stazione Esatto": { tipoSens: valore_soglia, ... }, ... }
#    Assicurati che i nomi stazione qui corrispondano ESATTAMENTE a quelli in STAZIONI_INTERESSATE
SOGLIE_PER_STAZIONE = {
    "Nevola": {
        100: 1.0,  # Livello Idrometrico (tipoSens 100) specifico per Nevola
        0: 60.0    # Esempio: Pioggia (tipoSens 0) specifica per Nevola (sovrascrive 50.0 generico)
    },
    "Misa": {
        100: 3.5   # Livello Idrometrico (tipoSens 100) specifico per Misa
    },
    "Ponte Garibaldi": {
        101: 1.5   # Livello Misa 2 (tipoSens 101) specifico per Ponte Garibaldi
    },
    "Arcevia": {
        # Nota: Anche se filtriamo per CODICE_ARCEVIA_CORRETTO, il nome usato qui
        # deve essere "Arcevia" per corrispondere al nome della stazione nei dati
        5: 32.0,   # Temperatura (tipoSens 5) specifica per Arcevia (sovrascrive 35.0 generico)
        100: 1.8   # Livello Idrometrico (tipoSens 100) specifico per Arcevia
    },
    "Serra dei Conti": {
         100: 1.2 # Livello Misa (tipoSens 100) specifico per Serra dei Conti
    }
    # Aggiungi altre stazioni e sensori specifici qui se necessario
}
# --- FINE NUOVA CONFIGURAZIONE SOGLIE ---


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# --- Funzioni Helper ---

def fetch_data(url):
    """Recupera dati JSON da un URL DISABILITANDO la verifica SSL."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    response = None
    try:
        logging.warning(f"Tentativo di richiesta a {url} con VERIFICA SSL DISABILITATA (verify=False).")
        response = requests.get(url, headers=headers, timeout=45, verify=False)
        logging.info(f"Richiesta a {url} - Status Code: {response.status_code}")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout as e:
        logging.error(f"Timeout durante la richiesta a {url}: {e}")
        return None
    except requests.exceptions.HTTPError as e:
        resp_text = e.response.text[:200] if e.response else "N/A"
        logging.error(f"Errore HTTP durante la richiesta a {url}: {e.response.status_code} - {resp_text}...")
        return None
    except requests.exceptions.ConnectionError as e:
         logging.error(f"Errore di connessione durante richiesta a {url}: {e}")
         return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Errore generico durante la richiesta a {url}: {e}")
        return None
    except json.JSONDecodeError as e:
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

def formatta_evento_allerta(evento_str):
    """Formatta la stringa evento:colore in modo leggibile."""
    try:
        nome, colore = evento_str.split(':')
        emoji_map = {"yellow": "🟡", "orange": "🟠", "red": "🔴"}
        nome_formattato = nome.replace("_", " ").capitalize()
        if colore in emoji_map: return f"{emoji_map[colore]} {nome_formattato} ({colore})"
        elif colore not in LIVELLI_ALLERTA_IGNORATI: return f"❓ {nome_formattato} ({colore})"
        else: return None
    except ValueError: return f"Evento malformato: {evento_str}"

# --- Logica Principale ---

def check_allerte():
    """Controlla le API di allerta e restituisce un messaggio se ci sono allerte rilevanti."""
    messaggi_allerta = []
    urls_allerte = {"OGGI": URL_ALLERTA_OGGI, "DOMANI": URL_ALLERTA_DOMANI}
    for tipo_giorno, url in urls_allerte.items():
        logging.info(f"Controllo allerte {tipo_giorno} da {url}...")
        data = fetch_data(url)
        if data is None:
            messaggi_allerta.append(f"⚠️ Impossibile recuperare dati allerta {tipo_giorno}.")
            continue
        allerte_rilevanti_giorno = []
        for item in data:
            area = item.get("area")
            eventi_str = item.get("eventi")
            if area in AREE_INTERESSATE_ALLERTE and eventi_str:
                eventi_list = eventi_str.split(',')
                eventi_formattati_area = [fmt for ev in eventi_list if (fmt := formatta_evento_allerta(ev.strip()))]
                if eventi_formattati_area:
                     allerte_rilevanti_giorno.append(f"  - *Area {area}*:\n    " + "\n    ".join(eventi_formattati_area))
        if allerte_rilevanti_giorno: messaggi_allerta.append(f"🚨 *Allerte Meteo {tipo_giorno}:*\n" + "\n".join(allerte_rilevanti_giorno))
        else: logging.info(f"Nessuna allerta meteo rilevante trovata per {tipo_giorno} nelle aree {AREE_INTERESSATE_ALLERTE}.")
    return "\n\n".join(messaggi_allerta) if messaggi_allerta else ""

def check_stazioni():
    """Controlla i dati delle stazioni meteo e verifica le soglie (specifiche e generiche)."""
    messaggi_soglia = []
    logging.info(f"Controllo dati stazioni da {URL_STAZIONI}...")
    data = fetch_data(URL_STAZIONI)
    if data is None:
        return "⚠️ Impossibile recuperare dati stazioni meteo."

    stazioni_trovate_interessanti = False
    for stazione in data:
        nome_stazione_raw = stazione.get("nome", "N/A")
        nome_stazione = nome_stazione_raw.strip() # Pulisce spazi bianchi extra
        codice_stazione = stazione.get("codice")

        is_arcevia = "Arcevia" in nome_stazione_raw # Controlla sul nome raw per sicurezza
        # Filtra stazioni di interesse
        if not ((is_arcevia and codice_stazione == CODICE_ARCEVIA_CORRETTO) or \
                (not is_arcevia and nome_stazione in STAZIONI_INTERESSATE)):
            continue # Passa alla prossima stazione se non è di interesse

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

                # --- LOGICA DI LOOKUP SOGLIA AGGIORNATA ---
                soglia_da_usare = None
                sorgente_soglia = "Nessuna" # Per logging/debug

                # 1. Prova soglia specifica per stazione (usa nome pulito) e sensore
                if nome_stazione in SOGLIE_PER_STAZIONE and tipoSens in SOGLIE_PER_STAZIONE[nome_stazione]:
                    soglia_da_usare = SOGLIE_PER_STAZIONE[nome_stazione][tipoSens]
                    sorgente_soglia = f"Specifica ({nome_stazione})"
                # 2. Altrimenti, prova soglia generica per sensore
                elif tipoSens in SOGLIE_GENERICHE:
                    soglia_da_usare = SOGLIE_GENERICHE[tipoSens]
                    sorgente_soglia = "Generica"

                # 3. Se una soglia è stata trovata, procedi al controllo
                if soglia_da_usare is not None:
                    logging.debug(f"Applicando soglia {soglia_da_usare} ({sorgente_soglia}) per sensore {tipoSens} a {nome_stazione}")
                    try:
                        if valore_str is not None and valore_str != "" and valore_str.lower() != 'nan':
                            valore_num = float(valore_str)
                            # Confronto (usa > per "strettamente maggiore")
                            if valore_num > soglia_da_usare:
                                msg = (f"📈 *Soglia Superata!* ({sorgente_soglia})\n" # Aggiunto tipo soglia
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
                    # Se nessuna soglia (né specifica né generica) è definita per questo tipoSens
                     logging.debug(f"Nessuna soglia specifica o generica trovata per sensore {tipoSens} a {nome_stazione}")
                # --- FINE LOGICA DI LOOKUP SOGLIA ---

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
        exit(1)

    messaggio_finale_allerte = check_allerte()
    messaggio_finale_soglie = check_stazioni()

    messaggio_completo = ""
    if messaggio_finale_allerte: messaggio_completo += messaggio_finale_allerte + "\n\n"
    if messaggio_finale_soglie: messaggio_completo += messaggio_finale_soglie

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
