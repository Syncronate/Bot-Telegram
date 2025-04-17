import requests
import time
import hmac
import hashlib
import json
import os

# --- Leggi le credenziali e le configurazioni Telegram dai segreti ---
API_KEY = os.environ.get("WEATHERLINK_API_KEY")
API_SECRET = os.environ.get("WEATHERLINK_API_SECRET")
# Usa i nomi esatti dei tuoi secret GitHub
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") # <--- Nome Secret GitHub
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  # <--- Nome Secret GitHub

# Verifica che tutti i segreti siano stati impostati
if not all([API_KEY, API_SECRET, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    missing = [k for k, v in {
        "WEATHERLINK_API_KEY": API_KEY,
        "WEATHERLINK_API_SECRET": API_SECRET,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN, # <--- Nome Secret GitHub
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID   # <--- Nome Secret GitHub
    }.items() if not v]
    print(f"Errore: Le seguenti variabili d'ambiente (secrets) mancano: {', '.join(missing)}")
    exit(1) # Esce con errore

# --- Informazioni Stazioni (ID e Nome) ---
STATIONS_INFO = [
    {"id": 177386, "name": "Montignano"},
    {"id": "b4a8d4b2-beeb-41ca-b25f-27bf2f932156", "name": "Sant'Angelo"},
    {"id": 177405, "name": "Scapezzano"}
]
# ---------------------------------------------

# --- SOGLIE DI ALLERTA ---
# !! PERSONALIZZA QUESTI VALORI !!
THRESHOLDS = {
    # Controlla le unità esatte nel JSON dell'API!
    'wind_speed': 40,         # Es: mph, km/h?
    'wind_gust_10_min': 60, # Es: mph, km/h?
    'rain_rate_mm': 10,      # mm/h
    'rain_day_mm': 25        # mm
}
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
        'parse_mode': 'MarkdownV2' # Usa Markdown per formattazione
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
    escape_chars = r'_*[]()~`>#+={}|.!-' # Inclusi punto, trattino, esclamativo
    # Converte prima in stringa per sicurezza
    return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))

# --- Ciclo Principale ---
print("--- Inizio controllo dati meteo e soglie ---")

alerts_to_send = [] # Lista per raccogliere i messaggi di allerta specifici

for station_info in STATIONS_INFO:
    station_id = station_info["id"]
    station_name = station_info["name"]
    # Applica l'escape al nome della stazione per l'uso nei messaggi e nei log
    safe_station_name = escape_markdown(station_name)
    print(f"\n---> Controllo dati per Stazione: {safe_station_name} (ID: {station_id}) <---")
    current_conditions_endpoint = f"/current/{station_id}"

    full_data = get_weatherlink_data(current_conditions_endpoint, API_KEY, API_SECRET)

    if full_data:
        print(f"Dati ricevuti per {safe_station_name}, controllo soglie...")
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

                        if current_value is not None and threshold_value is not None:
                            try:
                                if float(current_value) >= float(threshold_value):
                                    # Soglia superata! Prepara messaggio con escape per Markdown
                                    alert_detail = (
                                        f"*{safe_station_name}*: " # Nome stazione già con escape
                                        f"{escape_markdown(data_key.replace('_', ' ').title())} = " # Nome parametro con escape
                                        f"`{escape_markdown(current_value)}` " # Valore con escape, in formato codice
                                        f"\\(Soglia: `{escape_markdown(threshold_value)}`\\)" # Soglia con escape, tra parentesi con escape
                                    )
                                    print(f"  ALERT: {data_key} = {current_value} >= {threshold_value}")
                                    station_alerts.append(alert_detail)
                            except (ValueError, TypeError) as e:
                                print(f"  Attenzione: Impossibile confrontare {data_key} = '{current_value}' con soglia {threshold_value}. Errore: {e}")
                        # else: # Debug opzionale per valori mancanti
                            # if current_value is None: print(f"  Info: Valore '{data_key}' non trovato.")
                            # if threshold_value is None: print(f"  Info: Soglia per '{data_key}' non definita.")

                    alerts_to_send.extend(station_alerts)

                else:
                    print(f"  Errore: Nessun blocco 'data' trovato per {safe_station_name}")
            else:
                 print(f"  Errore: Nessun blocco 'sensors' trovato per {safe_station_name}")
        except Exception as e:
            print(f"  Errore durante il controllo soglie per {safe_station_name}: {e}")
    else:
        print(f"--- Fallito recupero dati (chiamata API) per {safe_station_name} ---")

# --- Invio Messaggio Telegram Consolidato ---
if alerts_to_send:
    print("\n--- Soglie superate! Preparazione messaggio Telegram... ---")
    # Titolo con escape
    final_message = "⚠️ *Allerta Meteo Superamento Soglie* ⚠️\n\n"
    final_message += "\n".join(alerts_to_send) # Ogni allerta è già formattata per Markdown

    # Invia usando i nomi corretti dei secret letti all'inizio
    send_telegram_message(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, final_message)
else:
    print("\n--- Nessuna soglia superata. Nessun messaggio Telegram inviato. ---")

print("\n--- Fine controllo dati meteo e soglie ---")
