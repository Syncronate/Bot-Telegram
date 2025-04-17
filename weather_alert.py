import requests
import time
import hmac
import hashlib
import json
import os

# --- Leggi le credenziali e le configurazioni Telegram dai segreti ---
API_KEY = os.environ.get("WEATHERLINK_API_KEY")
API_SECRET = os.environ.get("WEATHERLINK_API_SECRET")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Verifica che tutti i segreti siano stati impostati
if not all([API_KEY, API_SECRET, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    missing = [k for k, v in {
        "WEATHERLINK_API_KEY": API_KEY,
        "WEATHERLINK_API_SECRET": API_SECRET,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID
    }.items() if not v]
    print(f"Errore: Le seguenti variabili d'ambiente (secrets) mancano: {', '.join(missing)}")
    exit(1) # Esce con errore

# --- Informazioni Stazioni (ID e Nome) ---
STATIONS_INFO = [
    {"id": 177386, "name": "Montignano"}, # Nomi più brevi per Telegram
    {"id": "b4a8d4b2-beeb-41ca-b25f-27bf2f932156", "name": "Sant'Angelo"},
    {"id": 177405, "name": "Scapezzano"}
]
# ---------------------------------------------

# --- SOGLIE DI ALLERTA ---
# Modifica questi valori secondo le tue necessità!
THRESHOLDS = {
    # Le unità sono quelle fornite dall'API (controlla l'output JSON)
    # Wind speed (presumibilmente mph o nodi, verifica!)
    'wind_speed': 1,
    # Wind gust (presumibilmente mph o nodi, verifica!)
    'wind_gust_10_min': 60,
    # Rain rate (mm/h)
    'rain_rate_mm': 10,
    # Daily rain (mm)
    'rain_day_mm': 1
}
# Dati che vogliamo monitorare (devono corrispondere alle chiavi in THRESHOLDS)
DATA_TO_MONITOR = list(THRESHOLDS.keys())
# ------------------------

API_BASE_URL = "https://api.weatherlink.com/v2"

# --- Funzioni Helper ---

def get_weatherlink_data(endpoint_path, api_key, api_secret):
    """Effettua chiamata GET autenticata all'API WeatherLink V2."""
    current_timestamp = int(time.time())
    params_to_sign = {"api-key": api_key, "t": str(current_timestamp)}
    string_to_sign = "".join(key + params_to_sign[key] for key in sorted(params_to_sign.keys()))
    try:
        api_signature = hmac.new(api_secret.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
        final_params = {"api-key": api_key, "t": str(current_timestamp), "api-signature": api_signature}
        headers = {'X-Api-Secret': api_secret}
        full_url = f"{API_BASE_URL}{endpoint_path}"
        response = requests.get(full_url, params=final_params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Errore richiesta API per {endpoint_path}: {e}")
        if e.response is not None: print(f"  Status: {e.response.status_code}, Risposta: {e.response.text[:200]}...")
        return None
    except Exception as e:
        print(f"Errore imprevisto in get_weatherlink_data per {endpoint_path}: {e}")
        return None

def send_telegram_message(bot_token, chat_id, message):
    """Invia un messaggio a una chat Telegram tramite un bot."""
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'MarkdownV2' # Usa Markdown per formattazione (opzionale)
    }
    try:
        response = requests.post(api_url, data=payload, timeout=15)
        response.raise_for_status()
        print(f"Messaggio Telegram inviato con successo (Chat ID: {chat_id}).")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Errore invio messaggio Telegram: {e}")
        if e.response is not None:
             print(f"  Status: {e.response.status_code}, Risposta: {e.response.text[:200]}...")
        return False
    except Exception as e:
        print(f"Errore imprevisto in send_telegram_message: {e}")
        return False

def escape_markdown(text):
    """Effettua l'escape dei caratteri speciali per MarkdownV2 di Telegram."""
    # Da documentazione Telegram: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escape_chars = r'_*[]()~`>#+={}|.!' # Aggiunto trattino e punto
    return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))

# --- Ciclo Principale ---
print("--- Inizio controllo dati meteo e soglie ---")

alerts_to_send = [] # Lista per raccogliere i messaggi di allerta specifici

for station_info in STATIONS_INFO:
    station_id = station_info["id"]
    station_name = station_info["name"]
    print(f"\n---> Controllo dati per Stazione: {escape_markdown(station_name)} (ID: {station_id}) <---")
    current_conditions_endpoint = f"/current/{station_id}"

    full_data = get_weatherlink_data(current_conditions_endpoint, API_KEY, API_SECRET)

    if full_data:
        print(f"Dati ricevuti per {escape_markdown(station_name)}, controllo soglie...")
        station_alerts = [] # Alert specifici per questa stazione

        try:
            if full_data.get("sensors") and len(full_data["sensors"]) > 0:
                sensor_data_list = full_data["sensors"][0].get("data")
                if sensor_data_list and len(sensor_data_list) > 0:
                    core_data = sensor_data_list[0]

                    # Controlla ogni dato monitorato rispetto alla sua soglia
                    for data_key in DATA_TO_MONITOR:
                        current_value = core_data.get(data_key)
                        threshold_value = THRESHOLDS.get(data_key)

                        # Procedi solo se abbiamo un valore e una soglia definita
                        if current_value is not None and threshold_value is not None:
                            try:
                                # Confronta i valori numericamente
                                if float(current_value) >= float(threshold_value):
                                    # Soglia superata! Prepara messaggio per questa specifica allerta
                                    alert_detail = (
                                        f"*{escape_markdown(station_name)}*: "
                                        f"{escape_markdown(data_key.replace('_', ' ').title())} = "
                                        f"`{escape_markdown(current_value)}` "
                                        f"(Soglia: `{escape_markdown(threshold_value)}`)"
                                    )
                                    print(f"  ALERT: {alert_detail}")
                                    station_alerts.append(alert_detail)
                                else:
                                     # Soglia non superata (utile per debug, rimuovere se non serve)
                                     # print(f"  OK: {data_key} = {current_value} (Soglia: {threshold_value})")
                                     pass


                            except (ValueError, TypeError) as e:
                                print(f"  Attenzione: Impossibile confrontare {data_key} = '{current_value}' con soglia {threshold_value}. Errore: {e}")
                        # else:
                             # Valore non trovato nell'API o soglia non definita (utile per debug)
                             # if current_value is None: print(f"  Info: Valore '{data_key}' non trovato nell'API.")
                             # if threshold_value is None: print(f"  Info: Soglia per '{data_key}' non definita.")

                    # Aggiungi gli alert di questa stazione alla lista generale
                    alerts_to_send.extend(station_alerts)

                else:
                    print(f"  Errore: Nessun blocco 'data' trovato per {escape_markdown(station_name)}")
            else:
                 print(f"  Errore: Nessun blocco 'sensors' trovato per {escape_markdown(station_name)}")
        except Exception as e:
            print(f"  Errore durante il controllo soglie per {escape_markdown(station_name)}: {e}")
    else:
        print(f"--- Fallito recupero dati (chiamata API) per {escape_markdown(station_name)} ---")

# --- Invio Messaggio Telegram Consolidato ---
if alerts_to_send:
    print("\n--- Soglie superate! Preparazione messaggio Telegram... ---")
    # Costruisci il messaggio finale
    final_message = "⚠️ *Allerta Meteo Superamento Soglie* ⚠️\n\n"
    final_message += "\n".join(alerts_to_send) # Aggiunge ogni allerta su una nuova riga

    # Invia il messaggio
    send_telegram_message(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, final_message)
else:
    print("\n--- Nessuna soglia superata. Nessun messaggio Telegram inviato. ---")

print("\n--- Fine controllo dati meteo e soglie ---")
