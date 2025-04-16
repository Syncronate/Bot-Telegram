name: Meteo Marche Monitor - Stazioni

on:
  schedule:
    # Esegue ogni 30 minuti (usa di default lo script "thresholds")
    - cron: '*/30 * * * *'
  workflow_dispatch:
    inputs:
      script_mode:
        description: 'Scegli quale script eseguire'
        required: true
        type: choice
        options:
          - thresholds # Invia solo se soglie superate (DEFAULT per schedule)
          - full_report # Invia sempre report completo valori
        default: 'thresholds'

jobs:
  check_stations:
    name: Controllo Stazioni Meteo
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests

      - name: Determine Script to Run
        id: set_script # Diamo un ID a questo step per referenziarne l'output
        run: |
          SCRIPT_NAME="station_checker_thresholds.py" # Default per schedule e se input non è 'full_report'
          if [[ "${{ github.event_name }}" == "workflow_dispatch" && "${{ github.event.inputs.script_mode }}" == "full_report" ]]; then
            SCRIPT_NAME="station_checker_full_report.py"
          fi
          echo "script_file=$SCRIPT_NAME" >> $GITHUB_OUTPUT # Esporta variabile per gli step successivi

      - name: Run Selected Station Check Script
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        # Usa l'output dello step precedente per determinare quale script eseguire
        run: python ${{ steps.set_script.outputs.script_file }}

      - name: Check script execution status
        if: failure()
        run: echo "Script Station Check fallito!" && exit 1
