import requests
import os
import json
import logging
from datetime import datetime
import urllib3

# --- Configurazione Allerte ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# *** MODIFICA: Rimosso URL_ALLERTA_OGGI ***
URL_ALLERTA_DOMANI = "https://allertameteo.regione.marche.it/o/api/allerta/get-stato-allerta-domani"

AREE_INTERESSATE_ALLERTE = ["2", "4"] # Esempio: ["1", "2", "3", "4", "5", "6"] per tutte
LIVELLI_ALLERTA_IGNORATI = ["green", "white"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Funzioni Helper (Invariate dalla versione precedente, a parte formatta_evento_allerta già modificata) ---

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

def formatta_evento_allerta(evento_str):
    """Formatta la stringa evento:colore in modo leggibile, con colori in italiano."""
    try:
        nome, colore = evento_str.split(':')
        emoji_map = {"yellow": "🟡", "orange": "🟠", "red": "🔴"}
        color_translation_it = {
            "yellow": "giallo",
            "orange": "arancione",
            "red": "rosso"
        }
        nome_formattato = nome.replace("_", " ").capitalize()
        colore_italiano = color_translation_it.get(colore, colore)

        if colore in emoji_map:
            return f"{emoji_map[colore]} {nome_formattato} ({colore_italiano})"
        elif colore not in LIVELLI_ALLERTA_IGNORATI:
            return f"❓ {nome_formattato} ({colore_italiano})"
        else: # Livello ignorato (green, white)
            return None
    except ValueError:
        logging.warning(f"Trovato evento malformato durante la formattazione: {evento_str}")
        return f"Evento malformato: {evento_str}"

# --- Logica Principale Solo Allerte (MODIFICATA) ---

def check_allerte_domani():
    """Controlla le API di allerta per DOMANI e restituisce un messaggio se ci sono allerte rilevanti o errore fetch."""
    messaggi_allerta_domani = []
    url = URL_ALLERTA_DOMANI
    tipo_giorno = "DOMANI" # Fisso perché controlliamo solo domani

    logging.info(f"Controllo allerte {tipo_giorno} da {url}...")
    data = fetch_data(url)

    if data is None:
        # Restituisce solo il messaggio di errore per domani
        return f"⚠️ Impossibile recuperare dati allerta {tipo_giorno} da {URL_ALLERTA_DOMANI}."

    # Se il fetch è riuscito, processa i dati
    allerte_rilevanti_giorno = []
    for item in data:
        area = item.get("area")
        eventi_str = item.get("eventi")
        if area in AREE_INTERESSATE_ALLERTE and eventi_str:
            eventi_list = eventi_str.split(',')
            eventi_formattati_area = [fmt for ev in eventi_list if (fmt := formatta_evento_allerta(ev.strip()))]
            if eventi_formattati_area:
                 allerte_rilevanti_giorno.append(f"  - *Area {area}*:\n    " + "\n    ".join(eventi_formattati_area))

    if allerte_rilevanti_giorno:
         messaggi_allerta_domani.append(f"🚨 *Allerte Meteo RILEVANTI per {tipo_giorno}:*\n" + "\n".join(allerte_rilevanti_giorno))
    
    # Se non ci sono allerte rilevanti, restituisce stringa vuota
    # Altrimenti, restituisce i messaggi di allerta per domani
    if not messaggi_allerta_domani:
        return ""
    else:
        return "\n\n".join(messaggi_allerta_domani)

# --- Esecuzione Script Allerte (MODIFICATA) ---
if __name__ == "__main__":
    logging.info("--- Avvio Controllo ALLERTE Meteo Marche per DOMANI ---")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.critical("Errore: Le variabili d'ambiente TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID sono necessarie.")
        exit(1)

    # Esegui il check solo per domani
    messaggio_allerte = check_allerte_domani() # Modificata chiamata funzione

    messaggio_da_inviare = ""
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    footer = f"\n\n*{'='*30}*"

    # Caso 1: Ci sono messaggi (allerte rilevanti per domani o errore fetch)
    if messaggio_allerte:
        if "⚠️" in messaggio_allerte: # Indica errore fetch
             header = f"*{'='*5} ERRORE Recupero Allerte DOMANI ({timestamp}) {'='*5}*\n\n"
             messaggio_da_inviare = header + messaggio_allerte.strip() + footer
             logging.error(f"Errore recupero dati allerte DOMANI rilevato: {messaggio_allerte}")
        else: # Indica allerte rilevanti per domani
             header = f"*{'='*5} Report ALLERTE RILEVANTI DOMANI ({timestamp}) {'='*5}*\n\n"
             messaggio_da_inviare = header + messaggio_allerte.strip() + footer
             logging.info("Trovate allerte rilevanti per DOMANI da notificare.")

    # Caso 2: messaggio_allerte è vuoto (fetch OK, nessuna allerta rilevante per domani)
    else:
        header = f"*{'='*5} Report ALLERTE DOMANI ({timestamp}) {'='*5}*\n\n"
        # *** MODIFICA: Aggiornato messaggio "nessuna allerta" ***
        testo_ok = (f"✅ Nessuna allerta meteo rilevante (diversa da verde/bianco) "
                    f"prevista per DOMANI nelle aree monitorate "
                    f"({', '.join(AREE_INTERESSATE_ALLERTE)}).")
        messaggio_da_inviare = header + testo_ok + footer
        logging.info("Nessuna allerta meteo rilevante per DOMANI trovata (fetch OK). Invio messaggio di stato OK.")

    if messaggio_da_inviare:
        logging.info("Invio messaggio stato allerte a Telegram...")
        send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, messaggio_da_inviare)
    else:
         logging.warning("Nessun messaggio da inviare è stato preparato per Telegram.")

    logging.info("--- Controllo ALLERTE Meteo Marche per DOMANI completato ---")
