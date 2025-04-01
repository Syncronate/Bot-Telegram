import gspread
from google.oauth2.service_account import Credentials
import telegram
import os
import logging
import json # Per caricare le soglie da JSON
from datetime import datetime

# --- CONFIGURAZIONE (LEGGE DA VARIABILI D'AMBIENTE) ---
# Queste verranno impostate tramite i Secrets di GitHub
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_CREDENTIALS_JSON_CONTENT = os.environ.get("GOOGLE_CREDENTIALS_JSON")
GSHEET_NAME = os.environ.get("GSHEET_NAME")
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME")

# Carica le soglie dalla variabile d'ambiente THRESHOLDS_JSON
THRESHOLDS_JSON = os.environ.get("THRESHOLDS_JSON", '{}') # Default a JSON vuoto
THRESHOLDS = {} # Inizializza come dizionario vuoto

# --- Setup Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Funzioni Ausiliarie (Setup Client, Lettura GSheet, Invio Telegram) ---
# (Queste funzioni rimangono identiche alla versione precedente per GitHub Actions)

def check_required_env_vars():
    """Verifica che le variabili d'ambiente essenziali siano impostate."""
    required_vars = [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "GOOGLE_CREDENTIALS_JSON_CONTENT",
        "GSHEET_NAME",
        "WORKSHEET_NAME",
        "THRESHOLDS_JSON" # Aggiunto controllo per la configurazione delle soglie
    ]
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        logger.error(f"Errore: Variabili d'ambiente/Secrets mancanti: {', '.join(missing_vars)}")
        return False
    return True

def load_thresholds():
    """Carica e valida le soglie dal JSON."""
    global THRESHOLDS # Modifica la variabile globale
    try:
        loaded_thresholds = json.loads(THRESHOLDS_JSON)
        if not isinstance(loaded_thresholds, dict):
            raise ValueError("THRESHOLDS_JSON non contiene un dizionario JSON valido.")
        # Validazione aggiuntiva: assicurati che i valori siano numerici
        valid_thresholds = {}
        for key, value in loaded_thresholds.items():
            try:
                valid_thresholds[key] = float(value)
            except (ValueError, TypeError):
                 logger.warning(f"Valore soglia non valido per '{key}': '{value}'. Verrà ignorato.")
        THRESHOLDS = valid_thresholds
        if not THRESHOLDS:
            logger.warning("Nessuna soglia valida caricata da THRESHOLDS_JSON.")
        else:
             logger.info(f"Soglie caricate con successo: {len(THRESHOLDS)} colonne monitorate.")
        return True
    except json.JSONDecodeError:
        logger.error(f"Errore nel parsing di THRESHOLDS_JSON. Assicurati sia un JSON valido (es: {{\"ColonnaA\": 10, \"ColonnaB\": 25.5}}). Contenuto: {THRESHOLDS_JSON}")
        return False
    except ValueError as e:
        logger.error(e)
        return False

def setup_google_sheets_client_from_json():
    """Configura e restituisce il client gspread autenticato (usando JSON content)."""
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
        if not GOOGLE_CREDENTIALS_JSON_CONTENT:
             logger.error("Il contenuto delle credenziali JSON (GOOGLE_CREDENTIALS_JSON_CONTENT) è vuoto.")
             return None
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON_CONTENT)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        logger.info("Autenticazione Google Sheets riuscita (da JSON env var).")
        return client
    except json.JSONDecodeError:
        logger.error("Errore nel parsing del JSON delle credenziali Google.")
        return None
    except Exception as e:
        logger.error(f"Errore durante l'autenticazione Google Sheets da JSON: {e}")
        return None

def setup_telegram_bot():
    """Configura e restituisce l'istanza del bot Telegram."""
    try:
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        logger.info(f"Bot Telegram inizializzato. ID: {bot.get_me().id}")
        return bot
    except Exception as e:
        logger.error(f"Errore durante l'inizializzazione del bot Telegram: {e}")
        return None

def get_latest_row_data(gsheet_client):
    """Recupera l'ultima riga dal foglio specificato come dizionario."""
    try:
        spreadsheet = gsheet_client.open(GSHEET_NAME)
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        logger.info(f"Accesso a GSheet '{GSHEET_NAME}' -> Foglio '{WORKSHEET_NAME}'")

        # --- Metodo più efficiente per fogli grandi ---
        # 1. Ottieni l'header
        header = worksheet.row_values(1)
        if not header:
            logger.warning("Impossibile leggere l'header (riga 1) del foglio.")
            return None

        # 2. Trova l'ultima riga con dati (può essere lento su fogli *enormi*, ma meglio di get_all_records)
        #    Questo approccio cerca la prima cella non vuota dall'ultima colonna verso la prima,
        #    poi trova l'ultima riga non vuota in quella colonna. Funziona bene se i dati sono abbastanza densi.
        #    Un'alternativa più semplice ma potenzialmente meno precisa se ci sono righe vuote alla fine:
        #    last_row_index = len(worksheet.get_all_values(major_dimension='ROWS'))
        #    Questo è un metodo più robusto fornito da gspread per trovare l'ultima riga popolata:
        list_of_lists = worksheet.get_all_values() # Prende tutti i valori
        last_row_index = len(list_of_lists) # Indice dell'ultima riga (1-based per gspread.row_values)

        if last_row_index <= 1: # Solo header o foglio vuoto
             logger.warning("Il foglio di lavoro non contiene dati oltre all'header.")
             return None

        # 3. Leggi i valori dell'ultima riga
        latest_row_values = worksheet.row_values(last_row_index)

        # 4. Combina header e valori in un dizionario
        #    Assicurati che le liste abbiano la stessa lunghezza o gestisci la discrepanza
        latest_row_dict = {}
        for i, col_name in enumerate(header):
            if i < len(latest_row_values):
                latest_row_dict[col_name] = latest_row_values[i]
            else:
                latest_row_dict[col_name] = "" # O None, se preferisci

        logger.debug(f"Ultima riga letta (Indice {last_row_index}): {latest_row_dict}")
        return latest_row_dict
        # --- Fine metodo efficiente ---

        # Vecchio metodo (meno efficiente per fogli grandi):
        # all_records = worksheet.get_all_records() # header nella prima riga
        # if not all_records:
        #     logger.warning("Il foglio di lavoro è vuoto.")
        #     return None
        # latest_row = all_records[-1]
        # logger.debug(f"Ultima riga letta: {latest_row}")
        # return latest_row

    except gspread.exceptions.SpreadsheetNotFound:
        logger.error(f"Errore: Spreadsheet '{GSHEET_NAME}' non trovato. Controlla il nome.")
        return None
    except gspread.exceptions.WorksheetNotFound:
        logger.error(f"Errore: Foglio di lavoro '{WORKSHEET_NAME}' non trovato nello spreadsheet '{GSHEET_NAME}'.")
        return None
    except Exception as e:
        logger.error(f"Errore durante la lettura da Google Sheets: {e}", exc_info=True) # Logga traceback
        return None


def check_thresholds(row_data, thresholds_config):
    """Controlla i valori della riga rispetto alle soglie definite nella configurazione."""
    alerts = []
    if not row_data or not thresholds_config:
        logger.info("Nessun dato di riga o nessuna soglia configurata per il controllo.")
        return alerts

    logger.info(f"Controllo soglie per {len(thresholds_config)} colonne configurate.")
    for column_name, threshold in thresholds_config.items():
        # Assicurati che il nome colonna esista nel GSheet (case-sensitive!)
        if column_name not in row_data:
            logger.warning(f"La colonna '{column_name}' definita nelle soglie non esiste nell'header del foglio. Ignorata.")
            continue

        value_str = str(row_data.get(column_name, '')).strip()

        # Ignora valori vuoti o chiaramente non numerici come "N/A" prima di provare la conversione
        if not value_str or value_str.upper() == 'N/A':
            logger.debug(f"Valore vuoto o 'N/A' per la colonna '{column_name}'. Ignoro.")
            continue

        try:
            # Converti in numero, gestendo la virgola come separatore decimale
            value = float(value_str.replace(',', '.'))
        except ValueError:
            logger.warning(f"Valore '{value_str}' nella colonna '{column_name}' non è un numero valido dopo la pulizia. Ignoro.")
            continue

        # Controlla se il valore supera la soglia
        if value > threshold:
            alert_message = (
                f"⚠️ *Allarme Soglia Superata!*\n"
                # Metti in grassetto il nome della colonna per chiarezza
                f"*{column_name}*: `{value}` (Soglia: `{threshold}`)"
            )
            alerts.append(alert_message)
            logger.info(f"Soglia superata per '{column_name}': {value} > {threshold}")
        else:
             logger.debug(f"Valore OK per '{column_name}': {value} <= {threshold}")


    return alerts

def send_telegram_message(bot, chat_id, message):
    """Invia un messaggio tramite il bot Telegram."""
    if not bot:
        logger.error("Tentativo di inviare messaggio ma il bot non è inizializzato.")
        return False
    try:
        # Limita lunghezza messaggio (Telegram ha un limite di 4096 caratteri)
        max_len = 4090 # Lascia un po' di margine
        if len(message) > max_len:
            logger.warning("Messaggio troppo lungo, verrà troncato.")
            message = message[:max_len] + "\n... (troncato)"

        bot.send_message(chat_id=chat_id, text=message, parse_mode=telegram.ParseMode.MARKDOWN)
        logger.info(f"Messaggio inviato a chat ID {chat_id}.")
        return True
    except telegram.error.BadRequest as e:
        logger.error(f"Errore invio messaggio Telegram (BadRequest): {e} - Controlla CHAT_ID e formattazione messaggio.")
    except telegram.error.Unauthorized as e:
         logger.error(f"Errore invio messaggio Telegram (Unauthorized): {e} - Controlla BOT_TOKEN o se il bot è bloccato/non aggiunto alla chat.")
    except Exception as e:
        logger.error(f"Errore generico invio messaggio Telegram: {e}")
    return False

# --- Funzione Principale ---
def main():
    """Funzione principale eseguita una volta per ogni run di GitHub Action."""
    logger.info("Avvio controllo GSheet singolo...")

    if not check_required_env_vars():
        exit(1) # Esce se manca configurazione essenziale

    if not load_thresholds(): # Carica e valida le soglie all'inizio
         logger.error("Errore nel caricamento delle soglie definite in THRESHOLDS_JSON. Il bot non può procedere correttamente.")
         # Potresti decidere di uscire (exit(1)) o continuare senza soglie
         # exit(1) # Scegli questa se le soglie sono obbligatorie

    # Se non ci sono soglie valide, è inutile continuare? Dipende dalla logica desiderata.
    # if not THRESHOLDS:
    #     logger.info("Nessuna soglia valida configurata. Termino il controllo.")
    #     exit(0) # Uscita normale, nessun lavoro da fare

    gsheet_client = setup_google_sheets_client_from_json()
    bot = setup_telegram_bot()

    if not gsheet_client or not bot:
        logger.error("Impossibile procedere: errore inizializzazione client GSheet o Bot Telegram.")
        exit(1)

    latest_row = get_latest_row_data(gsheet_client)

    if latest_row:
        # Passa il dizionario THRESHOLDS alla funzione di controllo
        alerts = check_thresholds(latest_row, THRESHOLDS)

        if alerts:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Costruisci il messaggio di allarme
            alert_lines = [f"*{timestamp} - Controllo GSheet (GitHub Action)*\n"]
            alert_lines.extend(alerts)

            # Aggiungi opzionalmente l'intera riga per contesto (formattata)
            # try:
            #     row_details = json.dumps(latest_row, indent=2, ensure_ascii=False)
            #     alert_lines.append(f"\n\n*Dettagli riga completa:*\n```json\n{row_details}\n```")
            # except Exception as json_e:
            #     logger.warning(f"Impossibile formattare i dettagli della riga come JSON: {json_e}")
            #     alert_lines.append(f"\n\n*Dettagli riga (raw):*\n`{latest_row}`")

            full_alert_message = "\n".join(alert_lines)

            if not send_telegram_message(bot, TELEGRAM_CHAT_ID, full_alert_message):
                logger.error("Fallito invio della notifica di allarme a Telegram.")
                # Potresti uscire con errore per segnalare il fallimento nell'Action
                # exit(1)
            else:
                logger.info("Notifica di allarme inviata con successo.")
        else:
            logger.info("Nessuna soglia superata nell'ultima riga per le colonne monitorate.")
    else:
        logger.info("Nessun dato recuperato dall'ultima riga o foglio vuoto/solo header.")

    logger.info("Controllo GSheet singolo completato.")

if __name__ == "__main__":
    main()