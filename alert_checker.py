import requests
import os
import json
import logging
from datetime import datetime
import urllib3

# --- Configurazione Allerte ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

URL_ALLERTA_DOMANI = "https://allertameteo.regione.marche.it/o/api/allerta/get-stato-allerta-domani"
URL_ALLERTA_OGGI = "https://allertameteo.regione.marche.it/o/api/allerta/get-stato-allerta"

AREE_INTERESSATE_ALLERTE = ["2", "4"]
LIVELLI_ALLERTA_IGNORATI = ["green", "white"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Funzioni Helper (Invariate dalla versione precedente) ---

def fetch_data(url):
    """Recupera dati JSON da un URL DISABILITANDO la verifica SSL."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    response = None
    try:
        logging.warning(f"Tentativo di richiesta ALLERTE a {url} con VERIFICA SSL DISABILITATA (verify=False).")
        response = requests.get(url, headers=headers, timeout=45, verify=False)
        logging.info(f"Richiesta ALLERTE a {url} - Status Code: {response.status_code}")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout as e:
        logging.error(f"Timeout durante la richiesta ALLERTE a {url}: {e}")
        return None
    except requests.exceptions.HTTPError as e:
        resp_text = e.response.text[:200] if e.response else "N/A"
        logging.error(f"Errore HTTP durante la richiesta ALLERTE a {url}: {e.response.status_code} - {resp_text}...")
        return None
    except requests.exceptions.ConnectionError as e:
         logging.error(f"Errore di connessione durante richiesta ALLERTE a {url}: {e}")
         return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Errore generico durante la richiesta ALLERTE a {url}: {e}")
        return None
    except json.JSONDecodeError as e:
        resp_text = response.text[:200] if response else "N/A"
        resp_status = response.status_code if response else "N/A"
        logging.error(f"Errore nel decodificare JSON ALLERTE da {url}. Status: {resp_status}. Risposta (primi 200 char): '{resp_text}...' Errore: {e}")
        return None
    except Exception as e:
        logging.error(f"Errore imprevisto durante il fetch ALLERTE da {url}: {e}", exc_info=True)
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

# --- Funzione Helper MODIFICATA ---
def formatta_evento_allerta(evento_str):
    """Formatta la stringa evento:colore in modo leggibile, con colori in italiano."""
    try:
        nome, colore = evento_str.split(':')
        emoji_map = {"yellow": "🟡", "orange": "🟠", "red": "🔴"}
        # *** MODIFICA: Aggiunto dizionario per traduzione colori ***
        color_translation_it = {
            "yellow": "giallo",
            "orange": "arancione",
            "red": "rosso"
            # Altri colori (es. green, white) verranno lasciati in inglese se non ignorati
        }

        nome_formattato = nome.replace("_", " ").capitalize()
        # *** MODIFICA: Usa il colore tradotto se presente, altrimenti usa l'originale ***
        colore_italiano = color_translation_it.get(colore, colore)

        if colore in emoji_map:
            # *** MODIFICA: Usa colore_italiano nella stringa finale ***
            return f"{emoji_map[colore]} {nome_formattato} ({colore_italiano})"
        elif colore not in LIVELLI_ALLERTA_IGNORATI:
             # *** MODIFICA: Usa colore_italiano anche qui per coerenza ***
            return f"❓ {nome_formattato} ({colore_italiano})"
        else: # Livello ignorato (green, white)
            return None # Non mostrare questi livelli
    except ValueError:
        logging.warning(f"Trovato evento malformato durante la formattazione: {evento_str}")
        return f"Evento malformato: {evento_str}" # Ritorna un messaggio di errore per il debug

# --- Logica Principale Solo Allerte (Invariata) ---

def check_allerte_principale():
    """Controlla le API di allerta e restituisce un messaggio se ci sono allerte rilevanti o errore fetch."""
    messaggi_allerta = []
    urls_allerte = {"OGGI": URL_ALLERTA_OGGI, "DOMANI": URL_ALLERTA_DOMANI}
    fetch_fallito = False # Flag per tracciare fallimenti fetch

    for tipo_giorno, url in urls_allerte.items():
        logging.info(f"Controllo allerte {tipo_giorno} da {url}...")
        data = fetch_data(url)
        if data is None:
            messaggi_allerta.append(f"⚠️ Impossibile recuperare dati allerta {tipo_giorno}.")
            fetch_fallito = True # Segna che almeno un fetch è fallito
            continue # Passa al prossimo giorno

        # Se il fetch è riuscito, processa i dati
        allerte_rilevanti_giorno = []
        for item in data:
            area = item.get("area")
            eventi_str = item.get("eventi")
            if area in AREE_INTERESSATE_ALLERTE and eventi_str:
                eventi_list = eventi_str.split(',')
                # Applica la formattazione (ora con traduzione italiana)
                eventi_formattati_area = [fmt for ev in eventi_list if (fmt := formatta_evento_allerta(ev.strip()))]
                if eventi_formattati_area:
                     allerte_rilevanti_giorno.append(f"  - *Area {area}*:\n    " + "\n    ".join(eventi_formattati_area))

        if allerte_rilevanti_giorno:
             messaggi_allerta.append(f"🚨 *Allerte Meteo RILEVANTI {tipo_giorno}:*\n" + "\n".join(allerte_rilevanti_giorno))
        # Non aggiungiamo nulla se non ci sono allerte rilevanti per questo giorno

    # Se c'è stato un fallimento nel fetch, restituisci solo i messaggi di errore/allerta
    if fetch_fallito:
        return "\n\n".join(messaggi_allerta) # Conterrà gli errori e eventuali allerte dell'altro giorno
    # Se non ci sono stati fallimenti e non ci sono messaggi di allerta rilevanti, restituisci stringa vuota
    elif not messaggi_allerta:
        return ""
    # Altrimenti (nessun fallimento, allerte rilevanti trovate), restituisci i messaggi di allerta
    else:
        return "\n\n".join(messaggi_allerta)

# --- Esecuzione Script Allerte (Invariata) ---
if __name__ == "__main__":
    logging.info("--- Avvio Controllo SOLO ALLERTE Meteo Marche ---")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.critical("Errore: Le variabili d'ambiente TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID sono necessarie.")
        exit(1)

    # Esegui il check
    messaggio_allerte = check_allerte_principale()

    # Prepara il messaggio finale per Telegram
    messaggio_da_inviare = ""
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    footer = f"\n\n*{'='*30}*" # Footer comune

    # Caso 1: Ci sono messaggi (allerte rilevanti o errori fetch)
    if messaggio_allerte:
        # Controlla se è un messaggio di errore fetch (contiene l'emoji ⚠️)
        if "⚠️" in messaggio_allerte:
             header = f"*{'='*5} ERRORE Recupero Allerte ({timestamp}) {'='*5}*\n\n"
             messaggio_da_inviare = header + messaggio_allerte.strip() + footer
             logging.error(f"Errore recupero dati allerte rilevato: {messaggio_allerte}")
        else:
             # È un messaggio di allerte rilevanti
             header = f"*{'='*5} Report ALLERTE RILEVANTI ({timestamp}) {'='*5}*\n\n"
             messaggio_da_inviare = header + messaggio_allerte.strip() + footer
             logging.info("Trovate allerte rilevanti da notificare.")

    # Caso 2: messaggio_allerte è vuoto (fetch OK, nessuna allerta rilevante)
    else:
        header = f"*{'='*5} Report ALLERTE ({timestamp}) {'='*5}*\n\n"
        testo_ok = (f"✅ Nessuna allerta meteo rilevante (diversa da verde/bianco) "
                    f"prevista per oggi e domani nelle aree monitorate "
                    f"({', '.join(AREE_INTERESSATE_ALLERTE)}).")
        messaggio_da_inviare = header + testo_ok + footer
        logging.info("Nessuna allerta meteo rilevante trovata (fetch OK). Invio messaggio di stato OK.")

    # Invia il messaggio preparato (errore, allerta, o "tutto ok")
    # Il controllo if messaggio_da_inviare previene invii se per qualche motivo non è stato preparato
    if messaggio_da_inviare:
        logging.info("Invio messaggio stato allerte a Telegram...")
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_da_inviare)
    else:
         # Questo non dovrebbe accadere con la logica attuale, ma è una sicurezza
         logging.warning("Nessun messaggio da inviare è stato preparato per Telegram.")

    logging.info("--- Controllo SOLO ALLERTE Meteo Marche completato ---")
