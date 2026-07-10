# 5MG Analyzer — Web Dashboard + Pi-Backend

Wöchentlicher COT/Seasonality/Bond-Screener. Pi rechnet, GitHub Pages zeigt an
— exakt das adriawetter-Muster, nur mit `watchlist.json` statt `segelwetter`-HTML.

## 1. Einmaliges GitHub-Setup

1. **Repo anlegen:** github.com → New repository → `5mg-analyzer` → Public → Create
2. **Pages aktivieren:** Settings → Pages → Source: "Deploy from a branch" →
   `main` / `/ (root)` → Save. Läuft dann unter
   `https://<username>.github.io/5mg-analyzer/`
3. **Alle Dateien aus diesem Ordner hochladen:** im Repo → "Add file" →
   "Upload files" → kompletten Inhalt dieses Ordners reinziehen → Commit.
   (`index.html`, `scripts/`, `README.md` — `watchlist.json` entsteht erst
   durch den ersten Pi-Lauf, nicht vorher hochladen.)

## 2. Einmaliges Pi-Setup

```bash
# Deploy-Key erzeugen (damit der Pi pushen darf)
ssh-keygen -t ed25519 -C "hermes2-5mg-analyzer" -f ~/.ssh/id_5mg_deploy -N ""
cat ~/.ssh/id_5mg_deploy.pub
```

Public Key kopieren → GitHub-Repo → Settings → Deploy keys → Add deploy key
→ **"Allow write access" anhaken** → Add key.

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-5mg
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_5mg_deploy
    IdentitiesOnly yes
EOF

mkdir -p ~/hermes2/scripts
cd ~/hermes2/scripts
git clone git@github-5mg:<username>/5mg-analyzer.git 5mg_analyzer_repo
ln -s ~/hermes2/scripts/5mg_analyzer_repo/scripts ~/hermes2/scripts/5mg

pip install yfinance --break-system-packages   # falls noch nicht vorhanden
```

## 3. Testen

```bash
cd ~/hermes2/scripts/5mg
/home/pi/hermes2/venv/bin/python3 systemcheck_5mg.py
```

Erwartung: 5/5 ✅. Falls COT-Loader ❌ meldet: `cftc.gov` Erreichbarkeit vom
Pi aus prüfen (`curl -I https://www.cftc.gov`), das war aus der
Entwicklungsumgebung nicht testbar.

COT-Werte gegen Referenz prüfen (Report 2026-06-30 aus den Original-
Screenshots): CHF ≈ 89, NZD ≈ 84, CAD ≈ 84. Falls deutlich daneben:
`LOOKBACK_WEEKS` in `cot_loader.py` anpassen (Standard 26, Alternative 156).

## 4. Manueller Lauf

```bash
/home/pi/hermes2/venv/bin/python3 ~/hermes2/scripts/5mg_analyzer_repo/scripts/watchlist_export.py
```

Schreibt `watchlist.json` ins Repo und pusht automatisch. Dashboard zeigt
danach "Datenquelle: watchlist.json (live)" statt Demo-Daten.

Testlauf ohne Push: `... watchlist_export.py --no-push`

## 5. Cron einrichten

```bash
crontab -e
```

Zeile ergänzen (CFTC published freitags ~15:30 ET, hier 23:00 MESZ als
sicherer Puffer):

```
0 23 * * 5   /home/pi/hermes2/venv/bin/python3 /home/pi/hermes2/scripts/5mg_analyzer_repo/scripts/watchlist_export.py >> /home/pi/hermes2/logs/5mg_export.log 2>&1
```

## 6. Bond-Renditen pflegen

`~/hermes2/data/bonds_config.json` einmal wöchentlich von Hand aktualisieren
(z.B. investing.com → Rates & Bonds). Wird beim ersten Lauf automatisch mit
Startwerten angelegt.

## 7. Kalender anschließen (optional, sonst Dummy-Events)

`scripts/calendar_check.py` versucht `from morning_briefing import
get_high_impact_events`. Sobald diese Funktion in eurem bestehenden
ForexFactory-Setup existiert (Signatur `(ccy: str, tage: int) -> list[dict]`,
Rückgabe `{"dt": "...", "txt": "...", "impact": "high"}`), greift sie
automatisch — sonst laufen Platzhalter-Termine.

## Wichtig — alles hier ist ungetestet gegen Live-Daten

Diese Dateien wurden außerhalb eurer Netzwerk-Whitelist entwickelt
(cftc.gov, yfinance, investing.com waren von der Bau-Umgebung aus nicht
erreichbar). `systemcheck_5mg.py` ist der erste echte Test — bitte vor dem
Cron-Eintrag einmal manuell laufen lassen und die COT-Referenzwerte prüfen.
