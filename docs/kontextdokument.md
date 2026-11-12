# Kontextdokument: llama-monitor

> **Zweck dieses Dokuments:** Es ist die einzige Quelle der Wahrheit für eine Neuimplementierung
> des Projekts. Ein Umsetzer, der dieses Dokument liest, muss das System so bauen können, dass es
> sich **nicht unterscheidbar** vom Original verhält — gleiche Kommandos, gleiche Ausgaben,
> gleiche Exit-Codes, gleiche API-Antworten, gleiche Zustandsübergänge, gleiche Seiteneffekte.
> Kein Zugriff auf Original-Code wird vorausgesetzt; keine Rückfragen sind nötig.

**Sprachkonvention:** Fließtext Deutsch, alle Bezeichner (Befehle, Dateien, JSON-Schlüssel,
Fehlermeldungen) exakt wie spezifiziert — sie sind Bestandteil des beobachtbaren Verhaltens.

---

## 1. Projektübersicht

### 1.1 Zweck und Ziel

Das Projekt überwacht und steuert Instanzen eines externen Inferenz-Servers ("llama-server"),
der GGUF-Sprachmodelle über HTTP ausliefert. Konkret löst es:

1. **Lebenszyklus-Steuerung**: Starten und Stoppen des llama-server mit einer vordefinierten
   "Vorlage" (Template: Modellpfad, Binärpfad, Port und ~200 tuing-Parameter).
2. **Selbstheilung**: Ein Hintergrundprozess ("Daemon") prüft zyklisch den HTTP-Endpunkt
   `/health` des Servers und startet ihn bei Ausfall oder Crash neu.
3. **Fremdserver-Adoption**: Läuft auf dem Zielport bereits ein llama-server, den der Daemon
   nicht selbst gestartet hat, wird er "adoptiert" (nur überwacht, nicht getötet), außer die
   Health-Prüfung schlägt fehl.
4. **HTTP-API**: Ein lokaler REST-Endpunkt (Port 9500) erlaubt Statusabfragen, Template-Wechsel,
   Neustart-Signale, kontrolliertes Stoppen/Starten des Servers ("llm-stop"/"llm-start") und
   das Herunterfahren des Daemons — für die Integration in einen Chat-Gateway ("Hermes").
5. **Systemd-Integration**: Ein Untertehensbefehl erzeugt und aktiviert eine systemd-Unit für
   den Boot-Autostart des Daemons.
6. **Template-Verwaltung**: Interaktive Befehlszeilen-Assistenten ("Wizard") zum Erstellen,
   Kopieren, Bearbeiten und Löschen von Templates, inklusive automatischer Erkennung
   installierter llama-server-Binärdateien und Abfrage der tatsächlich unterstützten Parameter
   über `--help`-Introspektion.
7. **Log-Lesebefehl**: Ausgabe der Daemon- und llama-server-Protokolle aus dem systemd-Journal.

### 1.2 Domäne

Prozessüberwachung / Automatisierung im Bereich lokale LLM-Inferenz. Das Projekt ist **nicht**
der Inferenz-Server selbst; es orchestriert nur dessen Prozess.

### 1.3 Architektursicht (High-Level)

```
                 ┌─────────────────────────────────────────────────┐
                 │                 CLI (Befehlszeile)              │
                 │  start stop restart status logs llm-stop ...    │
                 │  Wizard: create-base create-from edit delete    │
                 │  enable-autostart (erzeugt systemd-Unit)        │
                 └──────────────┬──────────────────────────────────┘
                                │ (root-Elevation via pkexec/sudo)
                 ┌──────────────▼──────────────────────────────────┐
                 │                    DAEMON                       │
                 │  Hauptschleife (Zyklus check_interval_sec)      │
                 │  ├─ Health-Check-Loop (HTTP GET /health)        │
                 │  ├─ Prozesmonitor (Zombie-Erkennung, SIGTERM→SIGKILL)
                 │  ├─ Ereignis-Slots: restart / llm-stop / llm-start
                 │  └─ PID-Datei-Verwaltung                        │
                 └───────┬──────────────────────┬──────────────────┘
                         │                      │
        ┌────────────────▼───────┐   ┌──────────▼─────────────────────┐
        │  HTTP-API (127.0.0.1:  │   │  llama-server (Fremdprozess)   │
        │  9500, Hintergrundthread)  │  gestartet mit Argumenten aus  │
        │  /api/v1/* Endpunkte   │   │  der Parameter-Tabelle         │
        └────────────────────────┘   └────────────────────────────────┘

Gemeinsame Ebene:
  ├── KONFIGURATION: config.json + templates/*.json (Validierung, Persistenz)
  ├── PARAMETER-TABELLE: einzige Quelle für alle llama-server-Parameter
  │     (Schlüssel ↔ Flag, Typ, Vorgabewert, Optionen, Abhängigkeitsregeln)
  ├── HEALTH-CHECK: synchroner Wrapper um asynchrone HTTP-Prüfung
  └── PROZESSMONITOR: Prozessstatus via `ps`, Zombie-Behandlung, graceful kill
```

**Threading-Modell des Daemons:** ein Hauptthread (Schleife), ein API-Server-Thread
(Hintergrund, Dämon-Thread), ein Log-Lese-Thread pro gestartetem llama-server (Dämon-Thread),
plus kurzlebige Threads für Shutdown-Signal und Force-Kill-Wachhund (aus der API heraus).

**Daemon läuft im Vordergrund** (für systemd `Type=simple`); kein Doppel-Fork. Hintergrundstart
erfolgt durch systemd, `nohup` oder Shell-`&`.

### 1.4 Wichtige Design-Entscheidungen (mit Begründung und verworfenen Alternativen)

| Entscheidung | Begründung | Verworfene Alternative |
|---|---|---|
| **Eine zentrale Parameter-Tabelle** als einzige Quelle für alle llama-server-Parameter (Schlüssel, Flag, Typ, Vorgabe, Optionen, Abhängigkeitsregel) | Daemon (Argument-Emission) und Wizard (Prompts) lesen dieselbe Tabelle; ein neuer Parameter = genau ein Tabelleneintrag. Verhindert Drift zwischen Wizard und Daemon. | Zwei getrennte Listen in Daemon und Wizard (Fehleranfällig: Namen divergierten — historisch führte das zu falschen Flags wie `--penalty-*` statt `--repeat-penalty`). |
| **Regelbasierte Anwendbarkeit** (`when`-Prädikat je Parameter statt code-seitiger Sonderfälle) | Ein Parameter wird automatisch ausgeblendet/nicht emittiert, wenn ein verwandter Wert ihn widerspruchslos macht (z. B. `top_k` unter Mirostat). Veraltete Template-Schlüssel werden entfernt statt als widersprüchliche Flags übergeben. | Hardcodierte Prüfsuiten je Aufrufstelle. |
| **Daemon im Vordergrund** | systemd (`Type=simple`) übernimmt Lebenszyklus und Neustart (`Restart=always`). Kein Doppel-Fork- Daemonizing. | Selbst-Daemonisierender Prozess (doppelter Fork) — wurde entfernt, weil systemd sonst doppelt verwaltet. |
| **Rechte-Erhöhung ohne Terminal-Passwort-Prompt**: in grafischer Sitzung via `pkexec` (Polkit-Dialog), sonst `sudo`; `stop` bewusst ohne Rechte über die HTTP-API | Benutzbarkeit aus GUI-Agents/Chat-Integration; `stop` muss ohne Root funktionieren, weil die API jedem lokalen Nutzer offensteht. | Immer `sudo` (Terminal-Prompt blockt automatisierte Nutzung). |
| **Stop-Pfad-Budgets abgeleitet aus `restart_timeout_sec`** | Das Signal-Handling des Daemons stoppt zuerst den llama-server (SIGTERM-Wartezeit bis `restart_timeout_sec`, dann SIGKILL-Eskalation). Wer den Daemon früher SIGKILLed, verwaist den Server. Feste Budgets (z. B. 5 s API-Wachhund, 10 s CLI) waren die Ursache für verwaiste Server. | Feste Sekundenbudgets. |
| **Logs über das systemd-Journal** (`journalctl -u <unit>`) | Der Daemon schreibt auf stdout/stderr; systemd sammelt es. Keine eigene Log-Rotation nötig. | Eigene Logdateien. |
| **Wizard fragt die Binärdatei**: `--help`-Introspektion filtert die Tabelle auf tatsächlich unterstützte Flags, ersetzt Select-Listen durch die vom Build dokumentierten Werte, streicht nicht existierende `--no-x`-Formen. Fallback: statische Tabelle. | Unterschiedliche Builds kennen unterschiedliche Flags (z. B. Turbo-Quants). | Statisch ohne Introspektion (unbrauchbar bei Build-Varianten). |
| **Nur `httpx` als Laufzeit-Abhängigkeit** (andernfalls Standardbibliothek) | Async + synchroner HTTP-Client in einer Bibliothek; die API selbst nutzt die Standardbibliothek (einfacher threaded HTTP-Server). | Frameworks wie FastAPI/Flask (unnötiger Umfang). |
| **Zombie-Bewusstsein im Stop-Pfad**: Ein Zombie gilt als "terminiert"; Freigeben (reapen) ist Sache des Erzeugers über `wait()` | Ein nicht-gefreaptes Kind zählt in `ps` weiter als lebendig; sonst endlose Restart-Schleifen. SIGKILL auf Zombies ist wirkungslos und verbrennt das Budget. | `os.waitpid` in geteilten Hilfsfunktionen (zerstört den Rückgabecode-Status des Prozess-Handles; nur der Besitzer des Handles darf reapen). |

### 1.5 Eingesetzte Technologien (technisch; Logik ist stack-unabhängig beschrieben)

- **Laufzeit**: Python ≥ 3.10 (oder jede Runtime mit: Threads, Signalen, Unterprozesse,
  JSON, Datei-I/O, TCP-Sockets, HTTP-Client).
- **Laufzeit-Abhängigkeit**: `httpx` ≥ 0.28, < 1 (HTTP-Client, synchron + asynchron).
- **Standardbibliothek** (jede Runtime braucht Äquivalente): `json`, `os`, `signal`,
  `subprocess`, `threading`, `time`, `asyncio`, `dataclasses`, `pathlib`, `difflib`,
  `glob`, `shutil`, `re`, `http.server` (Threaded HTTP-Server für die API).
- **Externe Werkzeuge** (Linux): `ps` (Prozessstatus), `ss -tlnp` (Port→PID-Auflösung),
  `systemctl` (Unit-Verwaltung), `journalctl` (Log-Ausgabe), `pkexec`/`sudo` (Rechte).
- **Init-System**: systemd (Unit `Type=simple`, `Restart=always`, Journal als Log-Ziel).
- **Verpackung**: `setuptools`-basiert; Konsolenskript `llama-monitor` → Einstiegspunkt
  `llama_monitor.main:main`.

Die **Logik** in Abschnitt 3 ist bewusst so beschrieben, dass sie mit jedem Stack umsetzbar
ist, der die obigen Primitive bietet.

---

## 2. Voraussetzungen (Prerequisites)

### 2.1 Laufzeitumgebung

| Anforderung | Details |
|---|---|
| Betriebssystem | Linux mit systemd (für Autostart + `logs`-Befehl), `ps`, `ss`, optional `pkexec` |
| Runtime | Python ≥ 3.10 (Type-Hints, `dataclasses`, `time.monotonic`) |
| Netzwerk | localhost-TCP: API-Port (Standard 9500) + llama-server-Port der Templates (Standard 8080) |
| Dateisystem | Schreibrechte auf `~/.config/llama-monitor/` für die laufende User; Root für `/etc/systemd/system` |

### 2.2 Externe Abhängigkeiten

| Abhängigkeit | Version | Begründung |
|---|---|---|
| `httpx` | ≥ 0.28, < 1 | HTTP-Client für Health-Checks, Status-Abfragen und API-Aufrufe; synchron und asynchron aus einer Bibliothek. Keine andere Nicht-Standard-Abhängigkeit. |

### 2.3 Konfiguration: `~/.config/llama-monitor/config.json`

**Pfadauflösung des Home-Verzeichnisses (eine Quelle der Wahrheit, von allen Modulen gemeinsam
genutzt):**
1. Wenn die Umgebungsvariable `SUDO_USER` gesetzt ist: Home des genannten Nutzers
   (`/home/<SUDO_USER>`).
2. Wenn die `~`-Expansion zu `/root` auflöst (Prozess läuft als root, z. B. via pkexec, wo
   `SUDO_USER` gelöscht wird): scanne `/home/*` auf das erste Verzeichnis, das
   `.config/llama-monitor` enthält, und nimm dieses Home. (Wichtig: Die Reihenfolge der
   Nutzer in `/home/*` ist Implementierungsdetail; jedes gefundene Verzeichnis gewinnt.)
3. Sonst: normale `~`-Expansion.

> Diese Regel ist kritisch: Der Daemon läuft als root (via pkexec), die CLI als Nutzer.
> PID-Datei und Unit-Inhalte müssen ins **Nutzer**-Home landen, sonst findet die CLI den Daemon nie.

**Schema (alle Schlüssel der geladenen, validierten Konfiguration):**

| Schlüssel | Typ | Pflicht? | Vorgabe | Validierung |
|---|---|---|---|---|
| `enabled` | bool | ja | — | muss `bool` sein |
| `check_interval_sec` | Zahl | ja | — | Zahl (int/float) ≥ 1; wird zu `int` umgewandelt |
| `default_binary` | str | ja | — | nicht leer; Pfad wird `~`-expandiert und absolut aufgelöst; **muss existieren** |
| `autostart` | bool | nein | `false` | — |
| `restart_timeout_sec` | int | nein | `10` | — |
| `daemon_port` | int | nein | `9500` | — |
| `active_template` | str | nein | `""` | falls gesetzt: nicht leer **und** zugehörige Template-Datei muss existieren |
| `llm_stopped` | bool | nein | `false` | — |
| `log_file` | str | nein | — | falls gesetzt und nicht leer: `~`-expandiert/absolut |

**Genauer Text aller Konfigurations-Fehlermeldungen** (Ausnahme "ConfigError", Ausgabe auf
stderr als `Error: <Meldung>`):

- `Config file not found: <PFAD>`
- `Missing required config field: <SCHLÜSSEL>` (geprüft in Reihenfolge: enabled,
  check_interval_sec, default_binary)
- `'enabled' must be a boolean`
- `'check_interval_sec' must be a positive number`
- `'default_binary' must be a non-empty string`
- `'active_template' must be a non-empty string`
- `Active template '<NAME>' not found in templates directory`
- `Default binary not found: <PFAD>`
- `Templates directory not found: <PFAD>`

**Validierungsreihenfolge ist verbindlich** (bestimmt, welche Meldung bei mehreren Fehlern
erscheint): Datei existiert → JSON parse → Pflichtfelder (Reihenfolge oben) → Typen →
active_template → Pfad-Expansion → default_binary existiert → Templates-Verzeichnis existiert.

### 2.4 Konfiguration: `~/.config/llama-monitor/templates/<name>.json`

Ein Template pro Modell; Dateiname (ohne `.json`) ist der Template-Name.

**Validierungsregeln (in dieser Reihenfolge, Ausnahmetyp wie oben):**

1. `model_path` muss existieren (Schlüssel) → sonst `Template missing required field: 'model_path'`
2. `model_path` wird `~`-expandiert und absolut aufgelöst (mutiert beim Validieren!)
3. Die Modelldatei muss existieren → sonst `Model file not found: <PFAD>`
4. Falls `binary` gesetzt: expandiert/absolut + muss existieren → sonst
   `Template binary not found: <PFAD>`
5. Falls `port` gesetzt: muss `int` und 1 ≤ port ≤ 65535 sein → sonst `Invalid port in template: <port>`
6. Falls `bind` gesetzt: nicht-leerer String → sonst `Invalid bind address in template: <bind>`
7. Alle übrigen Felder sind frei (Parameter der Tabelle, siehe 3.3).

`load_template(name)`: Datei muss existieren → sonst `Template not found: <NAME>`; JSON parse;
validieren; dann Feld `name` auf den bestehenden Wert oder (falls fehlend) auf den Template-Namen
setzen. `list_template_names()`: sortierte Liste aller `*.json`-Stems im Verzeichnis (nicht-JSON
Dateien ignoriert; fehlendes Verzeichnis → leere Liste).

**Persistenz-Helfer** (beide: Datei muss existieren → sonst `Config file not found: <PFAD>`;
schreiben mit JSON, Einrückung 4, alle übrigen Felder bleiben erhalten):

- `save_active_template(name)`: prüft zuerst, dass `templates/<name>.json` existiert → sonst
  `Template '<NAME>' not found in templates directory`; setzt `active_template`.
- `save_llm_stopped(bool)`: setzt `llm_stopped`.

### 2.5 Konfiguration: `~/.config/llama-monitor/base.json`

Startwerte für den `create-base`-Wizard (beliebige Teilmenge der Template-Felder; typischerweise
`binary`, `bind`, `port`, Standard-Parameter). Fehlt die Datei: `Base template not found: <PFAD>`
und Exit 1.

### 2.6 Environment-Variablen

| Variable | Pflicht? | Wirkung |
|---|---|---|
| `HOME` | indirekt | Basis der `~`-Expansion; unter systemd-Root wird die Unit sie auf das Nutzer-Home setzen |
| `SUDO_USER` | optional | überschreibt die Home-Auflösung bei sudo-Ausführung (Regel 1 oben) |
| `DISPLAY` / `WAYLAND_DISPLAY` | optional | wenn gesetzt und `pkexec` verfügbar → GUI-Authentifizierungsdialog für Rechte-Erhöhung; sonst Fallback `sudo` |
| `PYTHONUNBUFFERED=1` | optional | wird in der systemd-Unit gesetzt (unverzögerter Journal-Output) |

### 2.7 Datenquellen (extern)

| Daten | Quelle |
|---|---|
| GGUF-Modelle | `~/models/` (nur Dateien mit Endung `.gguf`, sortiert) |
| llama-server-Binärdateien | Rekursiv `~/**/build/bin/llama-server` + `~/.lmstudio/extensions/backends/*/llama-server` + `PATH`-Treffer; dedupliziert über reale Pfade; sortiert |
| Laufende Server | `ps -eo pid,comm` / `ps -eo pid,args` / `ss -tlnp`; `/proc/<pid>/cmdline` (null-separiert), `/proc/<pid>/comm` |
| Server-Health | HTTP GET `http://<bind>:<port>/health` |
| Server-Modellinfo | HTTP GET `http://<bind>:<port>/v1/models` |
| systemd-Journal | `journalctl -u llama-monitor` |

### 2.8 Build- und Installationsprozess

1. Virtuelle Umgebung anlegen, Paket installierbar machen (setuptools ≥ 68 als Build-Backend;
   Paketname `llama-monitor`, Version `0.1.0`, Beschreibung
   `llama-server process monitor with health checking and systemd integration`).
2. Konsolenskript: Name `llama-monitor`, Ziel `llama_monitor.main:main`.
3. Systemweit aufrufbar machen: `llama-monitor` nach `/usr/local/bin/` kopieren oder symlinken
   (die systemd-Unit ruft genau diesen Pfad auf).
4. Konfigurationsverzeichnis `~/.config/llama-monitor/` mit `templates/` + `base.json` +
   `config.json` anlegen.
5. Autostart: `llama-monitor enable-autostart` als root (oder `--sudo` für pkexec) — erzeugt
   Unit + `daemon-reload` + `enable --now` (Inhalt exakt in Abschnitt 3.8.8).

### 2.9 Zugriffsrechte

- **Root nötig**: `start`, `restart`, `enable-autostart`, `disable-autostart` (schreiben nach
  `/etc/systemd/system`). Ohne Root beenden sich diese Befehle entweder mit einer Usage-Meldung
  (Exit 1) oder re-execen sich mit erhöhten Rechten (Details 3.8).
- **Kein Root nötig**: `stop` (PID-Signal bei eigener Berechtigung; sonst HTTP-API
  `/api/v1/shutdown` — die API antwortet jedem lokalen Nutzer), `status`, `logs`,
  `list-templates`, alle Wizard-Befehle, `models`, `set`, `llm-stop`, `llm-start`,
  `reload-template`.
- **Netzwerk**: Bind der API auf `127.0.0.1` (nur lokal).

---

## 3. Logik & Verhalten (Kernspezifikation)

### 3.1 Zentrale Begriffe

- **Template**: JSON-Datei unter `~/.config/llama-monitor/templates/<name>.json`; definiert
  Modell, Binärdatei, Netzwerk und alle llama-server-Parameter.
- **Aktives Template**: Das im Daemon gemerkte Template; Quellen: Daemon-Status (API) oder
  `active_template` in der Konfiguration.
- **Daemon**: Der Überwachungsprozess; verwaltet genau einen llama-server gleichzeitig.
- **Server-eigen vs. extern**: `server_owned` — `true`, wenn der Daemon den Prozess selbst
  gestartet hat (darf ihn töten); `false` bei adoptierten Fremdprozessen (nur überwachen).

### 3.2 CLI-Dispatch und Rechte-Erhöhung

**Aufruf**: `llama-monitor <subcommand> [args]`. Ohne Subcommand: allgemeine Hilfe ausgeben,
Exit 1. Unbekannter Subcommand: Hilfe + Exit 1. `KeyboardInterrupt` an einer beliebigen Stelle
des Einstiegspunkts → Exit 130. Jede andere unbehandelte Ausnahme → `Error: <Meldung>` auf
stderr, Exit 1.

**Prä-Verarbeitung (vor dem Argument-Parser):** Die Schreibweisen `--enable-autostart` und
`--disable-autostart` werden in die Subcommands `enable-autostart`/`disable-autostart`
übersetzt; enthält der Aufruf zusätzlich `--sudo`, wird das Flag aus der Argumentliste entfernt
und der Alias-Name (mit Unterstrich) verwendet, der das `--sudo`-Flag formal akzeptiert.

**Subcommands und Hilfe-Texte** (Kurzbeschreibung `help`):

| Subcommand | help-Text | Alias |
|---|---|---|
| `start` | Start the daemon (foreground) | — |
| `stop` | Stop the daemon | — |
| `restart` | Restart the daemon | — |
| `status` | Show daemon status (Option `--json`) | — |
| `list-templates` | List available model templates | — |
| `enable-autostart` | Enable autostart via systemd (Option `--sudo`) | `enable_autostart` |
| `disable-autostart` | Disable autostart (Option `--sudo`) | `disable_autostart` |
| `reload-template` | Change active template and restart (positional `template`, optional) | — |
| `create-base` | Create a new template from base.json | — |
| `create-from` | Create a template by copying an existing one (positional `template`, optional) | — |
| `edit` | Edit parameters of an existing template (positional `parameter`, optional) | — |
| `delete` | Delete an existing template (positional `template`, optional) | — |
| `models` | List GGUF models without a template | — |
| `set` | Interactive selector to set active_template | — |
| `logs` | Show daemon and llama-server logs (positional `lines`, int, default 40) | — |
| `llm-stop` | Stop llama-server (daemon won't restart it) | — |
| `llm-start` | Start llama-server (daemon resumes monitoring) | — |

**Rechte-Erhöhung**: `start`, `stop`, `restart` durchlaufen vor der Ausführung die
Elevationsprüfung; alle anderen nicht. Algorithmus:

1. Läuft der Prozess bereits mit UID 0 → nichts tun.
2. Kann der Befehl ohne Rechte erledigt werden? Nur `stop` (API-Pfad) → nichts tun, CLI
   behandelt es selbst.
3. Grafische Sitzung (`DISPLAY` oder `WAYLAND_DISPLAY` gesetzt) **und** `pkexec` verfügbar:
   re-exec über `pkexec <Runtime> -c <Bootstrap> <Paketwurzel> <argv…>`. Der Bootstrap-Code
   fügt die Paketwurzel in den Modulpfad ein und ruft den Einstiegspunkt mit den restlichen
   Argumenten auf (pkexec säubert die Umgebung, daher explizite Injektion). Rückgabecode des
   Kindes = Exit-Code des Vaters. Nur bei Exit-Codes 126/127 (Authentifizierung
   fehlgeschlagen/kein Dialog-Agent): Fallback auf `sudo`.
4. Fallback: `sudo <Runtime> -m llama_monitor.main <argv…>` (Modul-Aufruf statt Skript, weil
   sudo die Umgebung säubert). Kein sudo vorhanden → Fehler
   `Error: need root privileges but neither pkexec nor sudo is available.` (stderr, Exit 1).

> Die Unterbefehle `enable-autostart`/`disable-autostart` wenden denselben Mechanismus
> (**denselben Re-Exec-Pfad**) an, wenn sie mit `--sudo` ohne UID 0 aufgerufen werden — sie
> re-exec-en sich dann mit dem jeweiligen Subcommand als root. Ohne `--sudo` und ohne UID 0:
> Usage-Fehler (exakter Text in 3.8.8), Exit 1.

### 3.3 Parameter-Tabelle (PARAM_SPECS)

Die Tabelle definiert **alle** Parameter, die ein Template enthalten darf. Der Daemon emittiert
Argumente ausschließlich aus ihr; der Wizard baut seine Prompts ausschließlich aus ihr.

**Felddefinitionen je Eintrag:**

| Feld | Bedeutung |
|---|---|
| `key` | JSON-Schlüssel im Template |
| `flag` | llama-server-Flag (`--ctx-size`) |
| `kind` | `int` \| `float` \| `bool` \| `str` \| `select` |
| `default` | llama-server-Eigenwert (Grundlage fürs Pruning); `None` = "nicht gesetzt" |
| `options` | Auswahl (Wert, Anzeigetext) nur für `select` |
| `off_flag` | verneinte Form für Bools (`--no-webui`), falls der Build sie kennt |
| `when` | Prädikat über das bisherige Template; `false` = Parameter inaktiv (Prompt/Emitter überspringen, Pruning entfernt den Schlüssel) |
| `group` | Dialoggruppe im Wizard (Reihenfolge = Gruppenreihenfolge unten) |
| `always_ask` | true → wird im Wizard **immer** gefragt (Kernabfrage), auch in frischen Templates |

**Schlüsselkonventionen für die `when`-Spalte** (Abkürzungen für die gesamte Tabelle; alle
numerischen Vergleiche sind fehlertolerant: nicht-parsebarer/fehlender Wert → `0` bzw. `0.0`):

| Symbol | Regel (in Prosa) |
|---|---|
| — | kein Prädikat; immer aktiv |
| **DRAFT** | `spec_type` (Komma-getrennter String, Default `none`; `none`/Leerwert = aus) enthält mindestens einen Wert mit Präfix `draft-` |
| **NGSIM** | `spec_type` enthält `ngram-simple` |
| **NGMK** | `spec_type` enthält `ngram-map-k` |
| **NGK4V** | `spec_type` enthält `ngram-map-k4v` |
| **NGMOD** | `spec_type` enthält `ngram-mod` |
| **NGCACHE** | `spec_type` enthält `ngram-cache` |
| **MIRO** | `mirostat` > 0 (fehlend = 0) |
| **MIRO1** | `mirostat` == 1 |
| **!MIRO** | nicht MIRO (d. h. `mirostat` ≤ 0 oder fehlend) |
| **DTEMP** | `dynatemp_range` > 0 |
| **DRY** | `dry_multiplier` > 0 |
| **XTC** | `xtc_probability` > 0 |
| **ADAPT** | `adaptive_target` ≥ 0 |
| **YARN** | `rope_scaling` == `"yarn"` (Achtung: Schlüssel fehlt → `None` ≠ `"yarn"` → inaktiv) |
| **SPLIT** | `split_mode` != `"none"` (Schlüssel fehlt → `None` != `"none"` → **aktiv**) |
| **EMB** | `embedding` wahrheitsgemäß (fehlend = false) |
| **CP** | `cache_prompt` wahrheitsgemäß, **mit Vorgabe true bei fehlendem Schlüssel** |
| **CRAM** | `cache_ram` > 0 (fehlend = 0 → inaktiv) |
| **NOJ** | `json_schema` fehlt oder ist leer |
| **REASON** | `reasoning` nicht in `("off", None, False)`; fehlend = `"auto"` → aktiv |

**Gesamttabelle (206 Einträge, Reihenfolge verbindlich — sie bestimmt die
Argument-Emissionsreihenfolge, die Gruppenreihenfolge und die Wizard-Walks):**

Gruppe **`Server`** (8):

| # | key | flag | kind | default | off_flag | when | always_ask |
|---|-----|------|------|---------|----------|------|------------|
| 1 | port | `--port` | int | 8080 | — | — | ✔ |
| 2 | bind | `--host` | str | 127.0.0.1 | — | — | ✔ |
| 3 | api_prefix | `--api-prefix` | str | "" | — | — | |
| 4 | path | `--path` | str | "" | — | — | |
| 5 | timeout | `--timeout` | int | 3600 | — | — | |
| 6 | sse_ping_interval | `--sse-ping-interval` | int | 30 | — | — | |
| 7 | alias | `--alias` | str | "" | — | — | |
| 8 | tags | `--tags` | str | "" | — | — | |

Gruppe **`Access control`** (9):

| # | key | flag | kind | default | off_flag | when | always_ask |
|---|-----|------|------|---------|----------|------|------------|
| 9 | api_key | `--api-key` | str | "" | — | — | |
| 10 | api_key_file | `--api-key-file` | str | "" | — | — | |
| 11 | ssl_key_file | `--ssl-key-file` | str | "" | — | — | |
| 12 | ssl_cert_file | `--ssl-cert-file` | str | "" | — | — | |
| 13 | cors_origins | `--cors-origins` | str | * | — | — | |
| 14 | cors_methods | `--cors-methods` | str | "" | — | — | |
| 15 | cors_headers | `--cors-headers` | str | * | — | — | |
| 16 | cors_credentials | `--cors-credentials` | bool | true | `--no-cors-credentials` | — | |
| 17 | reuse_port | `--reuse-port` | bool | false | `--no-reuse-port` | — | |

Gruppe **`Server features`** (16):

| # | key | flag | kind | default | off_flag | when | always_ask |
|---|-----|------|------|---------|----------|------|------------|
| 18 | webui | `--webui` | bool | true | `--no-webui` | — | |
| 19 | metrics | `--metrics` | bool | false | — | — | |
| 20 | props | `--props` | bool | false | `--no-props` | — | |
| 21 | slots | `--slots` | bool | true | `--no-slots` | — | |
| 22 | agent | `--agent` | bool | false | `--no-agent` | — | |
| 23 | tools | `--tools` | str | "" | — | — | |
| 24 | mcp_servers_config | `--mcp-servers-config` | str | "" | — | — | |
| 25 | mcp_servers_json | `--mcp-servers-json` | str | "" | — | — | |
| 26 | ui_config | `--ui-config` | str | "" | — | — | |
| 27 | embedding | `--embedding` | bool | false | — | — | |
| 28 | rerank | `--rerank` | bool | false | — | — | |
| 29 | pooling | `--pooling` | select | "" | — | EMB | |
| 30 | embd_normalize | `--embd-normalize` | int | 2 | — | EMB | |
| 31 | sleep_idle_seconds | `--sleep-idle-seconds` | int | -1 | — | — | |
| 32 | special | `--special` | bool | false | — | — | |
| 33 | reverse_prompt | `--reverse-prompt` | str | "" | — | — | |

Gruppe **`Chat`** (17):

| # | key | flag | kind | default | off_flag | when | always_ask |
|---|-----|------|------|---------|----------|------|------------|
| 34 | jinja | `--jinja` | bool | true | `--no-jinja` | — | |
| 35 | chat_format | `--chat-format` | str | "" | — | — | |
| 36 | chat_template | `--chat-template` | str | "" | — | — | |
| 37 | chat_template_file | `--chat-template-file` | str | "" | — | — | |
| 38 | chat_template_kwargs | `--chat-template-kwargs` | str | "" | — | — | |
| 39 | reasoning | `--reasoning` | select | auto | — | — | |
| 40 | reasoning_format | `--reasoning-format` | select | auto | — | — | |
| 41 | reasoning_effort | `--reasoning-effort` | str | default | — | — | |
| 42 | reasoning_budget | `--reasoning-budget` | int | -1 | — | REASON | |
| 43 | reasoning_budget_message | `--reasoning-budget-message` | str | "" | — | REASON | |
| 44 | reasoning_preserve | `--reasoning-preserve` | bool | true | `--no-reasoning-preserve` | — | |
| 45 | skip_chat_parsing | `--skip-chat-parsing` | bool | false | `--no-skip-chat-parsing` | — | |
| 46 | prefill_assistant | `--prefill-assistant` | bool | true | `--no-prefill-assistant` | — | |
| 47 | grammar | `--grammar` | str | "" | — | NOJ | |
| 48 | grammar_file | `--grammar-file` | str | "" | — | NOJ | |
| 49 | json_schema | `--json-schema` | str | "" | — | — | |
| 50 | json_schema_file | `--json-schema-file` | str | "" | — | — | |

> `chat_format` ist ein **Legacy-Schlüssel**: bleibt in der Tabelle, damit alte Templates
> weiterwirken; der Wizard migriert ihn zu `chat_template` (Regel in 3.8.6).

Gruppe **`Context & cache`** (19):

| # | key | flag | kind | default | off_flag | when | always_ask |
|---|-----|------|------|---------|----------|------|------------|
| 51 | ctx_size | `--ctx-size` | select | 0 | — | — | ✔ |
| 52 | parallel | `--parallel` | int | -1 | — | — | ✔ |
| 53 | batch_size | `--batch-size` | int | 2048 | — | — | |
| 54 | ubatch_size | `--ubatch-size` | int | 512 | — | — | |
| 55 | predict | `--predict` | int | -1 | — | — | |
| 56 | keep | `--keep` | int | 0 | — | — | |
| 57 | swa_full | `--swa-full` | bool | false | — | — | |
| 58 | cont_batching | `--cont-batching` | bool | true | `--no-cont-batching` | — | |
| 59 | cache_prompt | `--cache-prompt` | bool | true | `--no-cache-prompt` | — | |
| 60 | cache_reuse | `--cache-reuse` | int | 0 | — | CP | |
| 61 | cache_ram | `--cache-ram` | int | 8192 | — | — | |
| 62 | cache_idle_slots | `--cache-idle-slots` | bool | true | `--no-cache-idle-slots` | CRAM | |
| 63 | kv_unified | `--kv-unified` | bool | true | `--no-kv-unified` | — | |
| 64 | kv_unified_per_slot | `--kv-unified-per-slot` | int | 0 | — | — | |
| 65 | ctx_checkpoints | `--ctx-checkpoints` | int | 32 | — | — | |
| 66 | checkpoint_min_step | `--checkpoint-min-step` | int | 8192 | — | — | |
| 67 | slot_prompt_similarity | `--slot-prompt-similarity` | float | 0.10 | — | — | |
| 68 | slot_save_path | `--slot-save-path` | str | "" | — | — | |
| 69 | context_shift | `--context-shift` | bool | false | `--no-context-shift` | — | |

Gruppe **`KV cache`** (4):

| # | key | flag | kind | default | off_flag | when |
|---|-----|------|------|---------|----------|------|
| 70 | cache_type_k | `--cache-type-k` | select | f16 | — | — |
| 71 | cache_type_v | `--cache-type-v` | select | f16 | — | — |
| 72 | lookup_cache_static | `--lookup-cache-static` | str | "" | — | NGCACHE |
| 73 | lookup_cache_dynamic | `--lookup-cache-dynamic` | str | "" | — | NGCACHE |

Gruppe **`CPU & threads`** (14):

| # | key | flag | kind | default | off_flag | when |
|---|-----|------|------|---------|----------|------|
| 74 | threads | `--threads` | int | -1 | — | — |
| 75 | threads_batch | `--threads-batch` | int | -1 | — | — |
| 76 | threads_http | `--threads-http` | int | -1 | — | — |
| 77 | cpu_mask | `--cpu-mask` | str | "" | — | — |
| 78 | cpu_range | `--cpu-range` | str | "" | — | — |
| 79 | cpu_strict | `--cpu-strict` | bool | false | — | — |
| 80 | cpu_mask_batch | `--cpu-mask-batch` | str | "" | — | — |
| 81 | cpu_range_batch | `--cpu-range-batch` | str | "" | — | — |
| 82 | cpu_strict_batch | `--cpu-strict-batch` | bool | false | — | — |
| 83 | prio | `--prio` | int | 0 | — | — |
| 84 | poll | `--poll` | int | 50 | — | — |
| 85 | prio_batch | `--prio-batch` | int | 0 | — | — |
| 86 | poll_batch | `--poll-batch` | int | 50 | — | — |
| 87 | numa | `--numa` | select | "" | — | — |

Gruppe **`GPU & offload`** (29):

| # | key | flag | kind | default | off_flag | when | always_ask |
|---|-----|------|------|---------|----------|------|------------|
| 88 | n_gpu_layers | `--n-gpu-layers` | str | auto | — | — | ✔ |
| 89 | split_mode | `--split-mode` | select | layer | — | — | |
| 90 | tensor_split | `--tensor-split` | str | "" | — | SPLIT | |
| 91 | main_gpu | `--main-gpu` | int | 0 | — | — | |
| 92 | device | `--device` | str | "" | — | — | |
| 93 | override_tensor | `--override-tensor` | str | "" | — | — | |
| 94 | fit | `--fit` | select | on | — | — | |
| 95 | fit_target | `--fit-target` | str | "" | — | — | |
| 96 | fit_ctx | `--fit-ctx` | int | 4096 | — | — | |
| 97 | n_cpu_moe | `--n-cpu-moe` | int | 0 | — | — | |
| 98 | n_cpu_ffn | `--n-cpu-ffn` | int | 0 | — | — | |
| 99 | cpu_moe | `--cpu-moe` | bool | false | `--no-cpu-moe` | — | |
| 100 | kv_offload | `--kv-offload` | bool | true | `--no-kv-offload` | — | |
| 101 | repack | `--repack` | bool | true | `--no-repack` | — | |
| 102 | op_offload | `--op-offload` | bool | true | `--no-op-offload` | — | |
| 103 | no_host | `--no-host` | bool | false | — | — | |
| 104 | flash_attn | `--flash-attn` | select | auto | — | — | |
| 105 | load_mode | `--load-mode` | select | auto | — | — | |
| 106 | lazy_mode | `--lazy-mode` | select | auto | — | — | |
| 107 | check_tensors | `--check-tensors` | bool | false | — | — | |
| 108 | rope_scaling | `--rope-scaling` | select | "" | — | — | |
| 109 | rope_scale | `--rope-scale` | float | 1.0 | — | — | |
| 110 | rope_freq_base | `--rope-freq-base` | float | 0.0 | — | — | |
| 111 | rope_freq_scale | `--rope-freq-scale` | float | 1.0 | — | — | |
| 112 | yarn_orig_ctx | `--yarn-orig-ctx` | int | 0 | — | YARN | |
| 113 | yarn_ext_factor | `--yarn-ext-factor` | float | -1.0 | — | YARN | |
| 114 | yarn_attn_factor | `--yarn-attn-factor` | float | -1.0 | — | YARN | |
| 115 | yarn_beta_slow | `--yarn-beta-slow` | float | -1.0 | — | YARN | |
| 116 | yarn_beta_fast | `--yarn-beta-fast` | float | -1.0 | — | YARN | |

Gruppe **`Multimodal`** (11):

| # | key | flag | kind | default | off_flag | when |
|---|-----|------|------|---------|----------|------|
| 117 | mmproj | `--mmproj` | str | "" | — | — |
| 118 | mmproj_url | `--mmproj-url` | str | "" | — | — |
| 119 | mmproj_auto | `--mmproj-auto` | bool | true | `--no-mmproj-auto` | — |
| 120 | mmproj_offload | `--mmproj-offload` | bool | true | `--no-mmproj-offload` | — |
| 121 | mmproj_device | `--mmproj-device` | str | "" | — | — |
| 122 | image_min_tokens | `--image-min-tokens` | int | 0 | — | — |
| 123 | image_max_tokens | `--image-max-tokens` | int | 0 | — | — |
| 124 | mtmd_batch_max_tokens | `--mtmd-batch-max-tokens` | int | 1024 | — | — |
| 125 | video_fps | `--video-fps` | float | 4.0 | — | — |
| 126 | video_timestamp_interval | `--video-timestamp-interval` | int | 5000 | — | — |
| 127 | video_ffmpeg_dir | `--video-ffmpeg-dir` | str | "" | — | — |

Gruppe **`LoRA`** (6):

| # | key | flag | kind | default | off_flag | when |
|---|-----|------|------|---------|----------|------|
| 128 | lora | `--lora` | str | "" | — | — |
| 129 | lora_scaled | `--lora-scaled` | str | "" | — | — |
| 130 | lora_init_without_apply | `--lora-init-without-apply` | bool | false | `--no-lora-init-without-apply` | — |
| 131 | control_vector | `--control-vector` | str | "" | — | — |
| 132 | control_vector_scaled | `--control-vector-scaled` | str | "" | — | — |
| 133 | control_vector_layer_range | `--control-vector-layer-range` | str | "" | — | — |

Gruppe **`Sampling`** (23):

| # | key | flag | kind | default | off_flag | when |
|---|-----|------|------|---------|----------|------|
| 134 | mirostat | `--mirostat` | int | 0 | — | — |
| 135 | temperature | `--temp` | float | 0.80 | — | — |
| 136 | dynatemp_range | `--dynatemp-range` | float | 0.0 | — | — |
| 137 | dynatemp_exp | `--dynatemp-exp` | float | 1.0 | — | DTEMP |
| 138 | top_k | `--top-k` | int | 40 | — | !MIRO |
| 139 | top_p | `--top-p` | float | 0.95 | — | !MIRO |
| 140 | top_nsigma | `--top-n-sigma` | float | -1.0 | — | !MIRO |
| 141 | typical_p | `--typical-p` | float | 1.0 | — | !MIRO |
| 142 | min_p | `--min-p` | float | 0.05 | — | — |
| 143 | xtc_probability | `--xtc-probability` | float | 0.0 | — | — |
| 144 | xtc_threshold | `--xtc-threshold` | float | 0.10 | — | XTC |
| 145 | adaptive_target | `--adaptive-target` | float | -1.0 | — | — |
| 146 | adaptive_decay | `--adaptive-decay` | float | 0.90 | — | ADAPT |
| 147 | repeat_last_n | `--repeat-last-n` | int | 64 | — | — |
| 148 | repeat_penalty | `--repeat-penalty` | float | 1.0 | — | — |
| 149 | presence_penalty | `--presence-penalty` | float | 0.0 | — | — |
| 150 | frequency_penalty | `--frequency-penalty` | float | 0.0 | — | — |
| 151 | seed | `--seed` | int | -1 | — | — |
| 152 | logit_bias | `--logit-bias` | str | "" | — | — |
| 153 | ignore_eos | `--ignore-eos` | bool | false | — | — |
| 154 | samplers | `--samplers` | str | "" | — | — |
| 155 | sampler_seq | `--sampler-seq` | str | "" | — | — |
| 156 | backend_sampling | `--backend-sampling` | bool | false | `--no-backend-sampling` | — |

Gruppe **`DRY sampling`** (5):

| # | key | flag | kind | default | off_flag | when |
|---|-----|------|------|---------|----------|------|
| 157 | dry_multiplier | `--dry-multiplier` | float | 0.0 | — | — |
| 158 | dry_base | `--dry-base` | float | 1.75 | — | DRY |
| 159 | dry_allowed_length | `--dry-allowed-length` | int | 2 | — | DRY |
| 160 | dry_penalty_last_n | `--dry-penalty-last-n` | int | 64 | — | DRY |
| 161 | dry_sequence_breaker | `--dry-sequence-breaker` | str | "" | — | DRY |

Gruppe **`Mirostat`** (2):

| # | key | flag | kind | default | off_flag | when |
|---|-----|------|------|---------|----------|------|
| 162 | mirostat_lr | `--mirostat-lr` | float | 0.10 | — | MIRO |
| 163 | mirostat_ent | `--mirostat-ent` | float | 5.00 | — | MIRO1 |

Gruppe **`Speculative decoding`** (33):

| # | key | flag | kind | default | off_flag | when |
|---|-----|------|------|---------|----------|------|
| 164 | spec_type | `--spec-type` | select | none | — | — |
| 165 | spec_draft_model | `--spec-draft-model` | str | "" | — | DRAFT |
| 166 | spec_draft_n_max | `--spec-draft-n-max` | int | 3 | — | DRAFT |
| 167 | spec_draft_n_min | `--spec-draft-n-min` | int | 0 | — | DRAFT |
| 168 | spec_draft_p_split | `--spec-draft-p-split` | float | 0.10 | — | DRAFT |
| 169 | spec_draft_p_min | `--spec-draft-p-min` | float | 0.00 | — | DRAFT |
| 170 | spec_draft_backend_sampling | `--spec-draft-backend-sampling` | bool | true | `--no-spec-draft-backend-sampling` | DRAFT |
| 171 | spec_draft_ngl | `--n-gpu-layers-draft` | str | "" | — | DRAFT |
| 172 | spec_draft_device | `--device-draft` | str | "" | — | DRAFT |
| 173 | spec_draft_threads | `--threads-draft` | int | 0 | — | DRAFT |
| 174 | spec_draft_threads_batch | `--threads-batch-draft` | int | 0 | — | DRAFT |
| 175 | spec_draft_cpu_mask | `--cpu-mask-draft` | str | "" | — | DRAFT |
| 176 | spec_draft_cpu_range | `--cpu-range-draft` | str | "" | — | DRAFT |
| 177 | spec_draft_cpu_strict | `--cpu-strict-draft` | bool | false | `--no-cpu-strict-draft` | DRAFT |
| 178 | spec_draft_prio | `--prio-draft` | int | 0 | — | DRAFT |
| 179 | spec_draft_poll | `--poll-draft` | int | 0 | — | DRAFT |
| 180 | spec_draft_cpu_moe | `--cpu-moe-draft` | bool | false | `--no-cpu-moe-draft` | DRAFT |
| 181 | spec_draft_n_cpu_moe | `--n-cpu-moe-draft` | int | 0 | — | DRAFT |
| 182 | spec_draft_override_tensor | `--override-tensor-draft` | str | "" | — | DRAFT |
| 183 | spec_draft_cache_type_k | `--cache-type-k-draft` | select | "" | — | DRAFT |
| 184 | spec_draft_cache_type_v | `--cache-type-v-draft` | select | "" | — | DRAFT |
| 185 | spec_ngram_simple_size_n | `--spec-ngram-simple-size-n` | int | 12 | — | NGSIM |
| 186 | spec_ngram_simple_size_m | `--spec-ngram-simple-size-m` | int | 48 | — | NGSIM |
| 187 | spec_ngram_simple_min_hits | `--spec-ngram-simple-min-hits` | int | 1 | — | NGSIM |
| 188 | spec_ngram_map_k_size_n | `--spec-ngram-map-k-size-n` | int | 12 | — | NGMK |
| 189 | spec_ngram_map_k_size_m | `--spec-ngram-map-k-size-m` | int | 48 | — | NGMK |
| 190 | spec_ngram_map_k_min_hits | `--spec-ngram-map-k-min-hits` | int | 1 | — | NGMK |
| 191 | spec_ngram_map_k4v_size_n | `--spec-ngram-map-k4v-size-n` | int | 12 | — | NGK4V |
| 192 | spec_ngram_map_k4v_size_m | `--spec-ngram-map-k4v-size-m` | int | 48 | — | NGK4V |
| 193 | spec_ngram_map_k4v_min_hits | `--spec-ngram-map-k4v-min-hits` | int | 1 | — | NGK4V |
| 194 | spec_ngram_mod_n_min | `--spec-ngram-mod-n-min` | int | 48 | — | NGMOD |
| 195 | spec_ngram_mod_n_max | `--spec-ngram-mod-n-max` | int | 64 | — | NGMOD |
| 196 | spec_ngram_mod_n_match | `--spec-ngram-mod-n-match` | int | 24 | — | NGMOD |

Gruppe **`Logging`** (10):

| # | key | flag | kind | default | off_flag | when |
|---|-----|------|------|---------|----------|------|
| 197 | verbosity | `--verbosity` | int | 3 | — | — |
| 198 | log_file | `--log-file` | str | "" | — | — |
| 199 | log_disable | `--log-disable` | bool | false | — | — |
| 200 | log_prefix | `--log-prefix` | bool | true | `--no-log-prefix` | — |
| 201 | log_timestamps | `--log-timestamps` | bool | true | `--no-log-timestamps` | — |
| 202 | log_colors | `--log-colors` | select | auto | — | — |
| 203 | log_verbose | `--log-verbose` | bool | false | `--no-log-verbose` | — |
| 204 | log_prompts_dir | `--log-prompts-dir` | str | "" | — | — |
| 205 | offline | `--offline` | bool | false | — | — |
| 206 | override_kv | `--override-kv` | str | "" | — | — |

**Optionslisten aller `select`-Parameter** (Reihenfolge verbindlich):

| key | Optionen (Wert = Anzeigetext) |
|---|---|
| pooling | `""` = "model default (unset)", `none`, `mean`, `cls`, `last`, `rank` |
| reasoning | `on`, `off`, `auto` |
| reasoning_format | `auto`, `none`, `deepseek`, `deepseek-legacy` |
| ctx_size | `0` = "0 (from model)"; dann `n*1024` = "`<n>`k (`<n*1024>`)" für n = 8, 16, 24, …, 256 → Werte 8192…262144 (32 Werte) |
| cache_type_k / cache_type_v | `f32, f16, bf16, q8_0, q4_0, q4_1, iq4_nl, q5_0, q5_1, turbo2, turbo3, turbo4` |
| numa | `""` = "unset", `distribute`, `isolate`, `numactl` |
| split_mode | `none` = "none (single GPU)", `layer`, `row`, `tensor` = "tensor (experimental)" |
| fit | `on`, `off`, `auto` |
| flash_attn | `on`, `off`, `auto` |
| load_mode | `""` = "server default (unset)", `auto`, `none`, `mmap`, `mlock`, `mmap+mlock`, `dio` |
| lazy_mode | `""` = "server default (unset)", `on`, `auto`, `off` |
| rope_scaling | `""` = "model default (unset)", `none`, `linear`, `yarn` |
| spec_type | `none` = "none (disabled)", `draft-simple`, `draft-eagle3`, `draft-mtp`, `draft-dflash`, `draft-dspark`, `ngram-simple`, `ngram-map-k`, `ngram-map-k4v`, `ngram-mod`, `ngram-cache` |
| log_colors | `on`, `off`, `auto` |
| spec_draft_cache_type_k / spec_draft_cache_type_v | `""` = "server default (unset)" + alle CACHE_TYPES (in obiger Reihenfolge) |

**Abgeleitete Strukturen:**
- `CORE_KEYS` (Kernabfrage des Wizards, 21 Schlüssel): port, bind, ctx_size, parallel,
  n_gpu_layers, split_mode, tensor_split, n_cpu_moe, flash_attn, load_mode, cache_type_k,
  cache_type_v, temperature, top_p, top_k, min_p, repeat_last_n, seed, spec_type, jinja, metrics.
  Zusätzlich gilt im Wizard: **jeder** Parameter mit `always_ask` zählt zur Kernabfrage.
- `key → flag` Abbildungstabelle und `key → Eintrag` Nachschlagetabelle.
- `GROUPS`: Reihenfolge der Gruppen = Reihenfolge des ersten Auftretens in der Tabelle:
  Server, Access control, Server features, Chat, Context & cache, KV cache, CPU & threads,
  GPU & offload, Multimodal, LoRA, Sampling, DRY sampling, Mirostat, Speculative decoding,
  Logging (15 Gruppen).

**Tabellenintegritätsregeln (invariant):** Schlüssel und Flags sind je einmalig; jeder
`select` hat mindestens eine Option; jede `bool`-Flag beginnt mit `--`; jedes gesetzte
`off_flag` ist exakt `--no-<flag ohne -->`; die Gruppenliste entspricht exakt der
Auftretensreihenfolge in der Tabelle.

**Hilfsfunktionen (exakte Semantik):**

- **`is_default(spec, value)`**: `true`, wenn Wert `None` oder `""` ist; sonst `false`, wenn
  `spec.default` `None` ist; sonst `str(value) == str(spec.default)` (String-Vergleich!).
- **`prune_defaults(template, extra_keys=())`**: Durchläuft die Tabelle in Reihenfolge; für
  jeden in `template` vorhandenen Spez-Schlüssel: wenn der Parameter inaktiv ist **oder** der
  Wert ein llama-server-Default ist → Schlüssel löschen und in Entfernt-Liste aufnehmen.
  `extra_keys` (Template-Metadaten wie `name`, `model_path`) werden nie angefasst. Rückgabe:
  `(mutiertes template, entfernter_schlüssel_liste)` (Reihenfolge = Tabellenreihenfolge).
- **`dependencies_of(spec, template)`**: Liste der Schlüssel, die das `when`-Prädikat liest —
  ermittelt durch Ausführen des Prädikats gegen ein Dictonary, das jeden Lesezugriff (get,
  Item-Zugriff, `in`-Operator) aufzeichnet. Ohne `when`: leere Liste. Rückgabe sortiert.
- **`candidate_values(template, key)`**: Kandidatenwerte zum Testen eines Gate-Schlüssels:
  für `select` alle Optionswerte ≠ `""`; für `bool` `[true, false]`; für `int`/`float`
  `[0, 1, 2, 3, -1]`; plus `spec.default` (falls nicht None); plus die Zusätze
  `true, false, "", "on", "off", 1, 2, 0.0` (ohne Duplikate, in dieser Reihenfolge).
- **`enabling_values(spec, template)`**: `{gate_schlüssel: wert}`, die das Prädikat von
  `false` auf `true` kippen: für jeden von `dependencies_of` gelesenen Schlüssel probiert die
  Funktion `candidate_values` der Reihe nach und nimmt den **ersten** Wert, der allein (im
  unveränderten Rest-Template) den Wechsel bewirkt. Nur Schlüssel, die das Prädikat wirklich
  liest.
- **`active_specs(template)`**: alle Spez mit `spec.active(template) == true`, in
  Tabellenreihenfolge.
- **`spec.active(template)`**: `when(template)` falls `when` gesetzt, sonst `true`.

### 3.4 Argument-Emission für den llama-server

**Funktion: Baue Argumentliste** (`_build_llama_args(template, config) → Liste[str]`).

Eingabe: `template` (validiertes Template-Dict), `config` (geladene Konfiguration).
Ausgabe: Argumentliste, deren **erstes Element die Binärdatei** ist (also ausführbar,
kein separates Programm-Argument).

Algorithmus (exakte Reihenfolge):

1. `binary` = `template["binary"]` falls vorhanden, sonst `config["default_binary"]`.
2. `bind` = `template["bind"]` falls vorhanden, sonst `"127.0.0.1"`.
3. `port` = `template["port"]` falls vorhanden, sonst `8080`.
4. Argumentliste beginnt mit `[binary, "--model", <model_path>, "--host", <bind>, "--port", str(port)]`.
   (`model_path` ist durch die Template-Validierung garantiert vorhanden; `port` als String.)
5. **Tabellen-Durchlauf**: Für jeden Eintrag der Parameter-Tabelle in Reihenfolge:
   - Schlüssel `port` oder `bind` → überspringen (schon emittiert).
   - Schlüssel nicht im Template → überspringen (fehlende Keys werden **nicht** mit
     Defaults gefüllt — "Absenz ist Absenz").
   - `when`-Prädikat falsch → überspringen (stale Keys werden stillschweigend fallengelassen,
     niemals als widersprüchliche Flags übergeben).
   - `kind == bool`: Wert wahr → `--flag` anhängen; Wert falsch **und** `off_flag` gesetzt →
     `--no-…` anhängen; sonst nichts. (Booleans mit falschem Wert **ohne** off_flag emittieren
     gar nichts — die Abwesenheit des Flags ist die Verneinung.)
   - Alle anderen Kinds: Wert `None` oder `""` → überspringen; sonst `[--flag, str(wert)]`
     anhängen (Zahlen via String-Konvertierung, Floats mit ihrer nativen Repräsentation).
6. Rückgabe der Liste.

> **Wichtig:** Der Tabellen-Durchlauf folgt der exakten Reihenfolge aus 3.3 — damit ist die
> Reihenfolge der Flags im erzeugten Prozess-Aufruf deterministisch und testbar.

**Hilfsfunktion `append_kv`** (`_add_kv(args, template, key, flag)`): hängt
`[flag, str(template[key])]` an, wenn `key` im Template vorhanden ist; sonst nichts.
(Sie existiert für Sonderfälle außerhalb des Tabellen-Durchlaufs.)

### 3.5 Daemon

#### 3.5.1 Daemon-Zustand (Singleton)

Ein Datenobjekt mit exakt diesen Feldern:

| Feld | Typ | Initial | Bedeutung |
|---|---|---|---|
| `pid` | int/null | null | PID des überwachten llama-server (null = keiner) |
| `active_template` | str/null | null | aktiver Template-Name (Dateiname ohne .json) |
| `active_template_name` | str/null | null | Anzeigename (Feld `name` im Template) |
| `model_path` | str/null | null | Modellpfad für Statusanzeige |
| `process_state` | str | `"stopped"` | siehe Zustandsmaschine unten |
| `health_ok` | bool | false | Ergebnis des letzten Health-Checks |
| `server_owned` | bool | false | true = Daemon hat den Server selbst gestartet |
| `restart_event` | Ereignis-Semaphor | nicht gesetzt | API→Daemon: Neustart anfordern |
| `shutdown_event` | Ereignis-Semaphor | nicht gesetzt | API→Daemon: herunterfahren |
| `llm_stopped` | bool | false | true = Server gestoppt halten, nicht wiederherstellen |
| `llm_stop_event` | Ereignis-Semaphor | nicht gesetzt | API→Daemon: llm-stop ausführen |
| `llm_start_event` | Ereignis-Semaphor | nicht gesetzt | API→Daemon: llm-start ausführen |

Zugriff ausschließlich über `get_global_state()` (liefert das Modul-Singleton) /
`set_global_state(state)` (setzt es, threadsicher). **Niemand** erzeugt eine eigene
Instanz direkt als Ersatz für das Singleton.

Zulässige Werte von `process_state` (alphabetisch in Kommentaren, real genutzt):
`stopped`, `starting`, `running`, `crashed`, `checking`, `waiting`, `killing`.

#### 3.5.2 PID-Datei

Pfad: `<aufgelöstes Home>/.config/llama-monitor/llama-monitor.pid`, wobei das Home **exakt**
die Regel aus 2.3 befolgt (Delegation an die Konfigurationsauflösung — nie eine zweite
Implementierung). Operationen:

- `write_pid(pid)`: Verzeichnis anlegen (falls fehlend), Inhalt = dezimale PID ohne Zeilenumbruch.
- `read_pid()`: fehlende Datei → null; nicht-parsebarer Inhalt oder I/O-Fehler → null; sonst int.
- `remove_pid()`: Datei löschen, falls vorhanden; I/O-Fehler stillschweigend ignorieren.

#### 3.5.3 `find_llama_server_by_port(port) → Dict|null`

Führt `ss -tlnp` aus (5 s Timeout, Textausgabe). Für jede Zeile:
1. Zeile muss `:<port> ` (Port gefolgt von Leerzeichen) oder `:<port>\t` (Tab) enthalten —
   das verhindert Treffer auf z. B. `:80801`.
2. Zeile muss `pid=` enthalten. PID = Teilstring zwischen `pid=` und dem nächsten Komma,
   als int geparst.
3. Prozessname: Inhalt von `/proc/<pid>/comm` (getrimmt), bei Lese-/Rechtefehlern `"unknown"`.
4. Kommandozeile: `/proc/<pid>/cmdline`, null-separiert gesplittet; die **ersten 3** Tokens,
   mit Leerzeichen verbunden; wenn es mehr als 3 Tokens gibt, `"..."` anhängen.
5. Rückgabe `{"pid": <int>, "process_name": <str>, "cmdline": <str>}` für den **ersten**
   passenden Eintrag.

Fehlerfälle (Timeout von `ss`, fehlendes `ss`-Binary, Parsefehler): `null`.

#### 3.5.4 `Daemon.start(template_name) → bool`

1. Template laden (Validierung läuft mit). Fehler →
   `Error loading template '<name>': <Meldung>` auf **stderr**, Rückgabe false.
2. Zustand setzen: `active_template` = Template-Name, `active_template_name` =
   `template["name"]` oder Template-Name; `_health_url` = `http://<bind>:<port>/health`;
   `state.model_path` = Template-`model_path`; `process_state` = `"starting"`.
3. Argumentliste bauen (3.4). Ausgaben (jeweils flush):
   - `Starting llama-server with template: <Anzeigename>`
   - `Args: <mit Leerzeichen verbundene Argumentliste>`
   - `Health URL: <health_url>`
4. Prozess starten: stdin =/dev/null, stdout gepiped (zeilenweise gepuffert, Textmodus),
   stderr **auf stdout umgeleitet**, `close_fds`, **neue Sitzung** (eigene Prozessgruppe).
5. Log-Lese-Thread starten (Dämon-Thread): liest stdout zeilenweise bis EOF und gibt jede
   Zeile als `[llama-server] <Zeile>` aus (getrimmt auf rechte Zeilenenden); Exceptions still.
6. **Crash-Sonde**: bis zu 5 s lang alle 0.25 s prüfen, ob der Prozess lebt
   (nicht-blockierendes Poll, **kein** blockierendes `wait()` — ein blockierendes Warten
   würde SIGTERM-Behandlung im Hauptthread um bis zu 5 s verzögern und dem
   Herunterfahr-Pfad das Zeitfenster für den graceful Server-Stopp rauben).
   - Läuft nach 5 s noch: `process_state = "running"`, Ausgabe
     `llama-server started successfully (PID <pid>)`.
   - Exit-Code 0 innerhalb der Sonde: `process_state = "running"` + Ausgabe
     `llama-server started (PID <pid>) - exited cleanly? Checking again...` (Weiterüberwachung).
   - Exit-Code ≠ 0 innerhalb der Sonde: `process_state = "crashed"` + Ausgabe auf stderr
     `llama-server crashed with code <code>`. **Trotzdem Rückgabe true** (der
     Recovery-Zyklus übernimmt).
7. Binary fehlt (`FileNotFoundError`): `process_state = "stopped"`,
   stderr `Binary not found: <binary>`, Rückgabe false.
8. Andere Startfehler: `process_state = "stopped"`, stderr
   `Failed to start llama-server: <Meldung>`, Rückgabe false.
9. Erfolg (Schritte 4–6 ohne Exception): Zustand `pid` = Prozess-PID,
   `started_server_pid` = PID, `server_owned` = true; Rückgabe true.

#### 3.5.5 `Daemon.stop(timeout_sec=10, force_kill=False) → bool`

1. Kein `state.pid` → Rückgabe true (nichts zu stoppen).
2. Wenn `force_kill` **oder** `server_owned`:
   a. `process_state = "killing"`; Ausgabe `Stopping llama-server (PID <pid>)...`.
   b. Graceful Kill (3.9) mit `timeout_sec`.
   c. **Kind reapen**: bis zu 3 s blockierend auf das Prozess-Handle warten (nur der
      Besitzer des Handles darf das — verhindert Zombies; Timeout → nichts unternehmen,
      der Kill hat bereits eskaliert; andere Fehler still).
   d. Erfolg: `process_state = "stopped"`, `pid` = null, `health_ok` = false,
      `server_owned` = false, PID-Datei löschen, Ausgabe `llama-server stopped.`
   e. Misserfolg: stderr `WARNING: Could not stop llama-server (PID <pid>)`,
      `process_state = "stopped"` (kein weiterer Zustand).
3. Sonst (extern, kein Force): Ausgabe
   `Releasing monitoring of external llama-server (PID <pid>).`; Zustand `pid` = null,
   `server_owned` = false, `health_ok` = false, PID-Datei löschen; der Prozess läuft weiter.
4. Prozess-Handle des Daemons auf null setzen; Rückgabe true.

#### 3.5.6 `Daemon.restart(force_kill=False) → bool`

`stop(force_kill=force_kill, timeout_sec=restart_timeout_sec)` dann, wenn
`active_template` gesetzt: `start(active_template)`; sonst false.

#### 3.5.7 `Daemon.check_and_recover()` — Entscheidungsbaum pro Zyklus

```
1. Wenn state.llm_stopped (llm-stop aktiv):
     a. pid gesetzt UND process_state nicht in ("stopped","killing"):
        Ausgabe "llm-stop active: stopping llama-server (daemon will not restart it)."
        → stop(restart_timeout_sec)
     b. kein pid: process_state = "stopped"
     c. ENDE (nichts wiederherstellen!)
2. pid = state.pid
3. aktives Template gesetzt?
     ja → Template laden; tpl_port = port oder 8080;
          existing = find_llama_server_by_port(tpl_port)   [Fehler → existing = null]
     nein → tpl_port = null; existing = null
4. Wenn nicht pid UND nicht tpl_port: ENDE (kein Template konfiguriert)
5. Wenn nicht pid UND nicht existing:
     aktives Template gesetzt → Ausgabe
       "No llama-server running. Starting with template..." → start(active_template)
     ENDE
6. Wenn existing UND nicht pid:  (externer Server gefunden — ADOPTION)
     port = tpl_port oder 8080
     _health_url fehlt → "http://127.0.0.1:<port>/health"
     Ausgabe "Found external llama-server on port <port> (PID <pid>). Monitoring it."
     state.pid = gefundene PID; server_owned = false
     (weiter zu Schritt 7 — Health-Check läuft mit!)
7. Wenn pid:
     health = await_health(_health_url); state.health_ok = health
     ok → process_state = "running"; ENDE
     nicht ok → process_state = "checking"; ps_state = Prozessstatus(pid)
       - Prozess tot   → "llama-server not running (PID <pid> gone). Restarting..."
                         state.pid = 0; PID-Datei löschen; start(active_template)
       - Zombie        → "llama-server is zombie (PID <pid>). Killing..."
                         stop(timeout=1); start(active_template)
       - sonst (lebt, unhealthy) → "llama-server unhealthy (PID <pid>). Restarting..."
                         restart()
8. Sonst (nicht pid UND server_owned):   (eigner Server verschwunden aus state.pid)
     tpl_port gesetzt und Port-Scan findet Server → Ausgabe
       "llama-server restarted externally (PID <pid>). Monitoring."
       state.pid = gefundene PID; server_owned = false
```

#### 3.5.8 `run_daemon(config)` — Hauptschleife

1. Daemon-Objekt erzeugen; neues globales Zustandsobjekt setzen; dessen
   llm-stop/llm-start-Ereignisse an den Daemon binden.
2. Wiederherstellen: `active_template` aus der Konfiguration (falls gesetzt) in den Zustand;
   `llm_stopped` aus der Konfiguration; falls true, Ausgabe
   `Restored llm-stop state: daemon started with llama-server stopped.`
3. Signal-Handler für SIGTERM und SIGINT registrieren: Ausgabe
   `\nReceived signal <nummer>, shutting down...`; shutdown_event setzen; wenn der
   Health-Event-Loop existiert und nicht geschlossen ist: **asynchron vom Signal-Thread aus**
   dessen Stopp aufrufen (um ein blockierendes Health-Warten zu unterbrechen); dann
   `SystemExit(0)` werfen (bricht die Schleife → finally-Block).
4. Ausgaben: `llama-monitor daemon starting (PID <pid>)` und
   `Config: interval=<check_interval_sec>s, timeout=<restart_timeout_sec>s` (jeweils flush).
5. Eigene PID schreiben (3.5.2).
6. API-Server als Dämon-Thread starten (3.7) mit `daemon_port` und Host `127.0.0.1`; Ausgabe
   `API server started on 127.0.0.1:<port>`.
7. **Autostart/Adoption beim Start**: wenn `config["autostart"]` und aktives Template:
   Port-Scan; Server vorhanden → Ausgabe
   `Autostart: llama-server already running on port <port> (PID <pid>). Adopting for monitoring.`
   und Adoption (health_url = `http://127.0.0.1:<port>/health`, Zustand pid/server_owned=false);
   sonst Ausgabe `Autostart enabled - launching template...` und `start(aktives Template)`.
8. **Schleife** (läuft bis shutdown_event):
   - restart_event gesetzt? → löschen, Ausgabe `Restart signal received.`, `restart()`, nächster Zyklus.
   - llm_stop_event gesetzt? → löschen, Ausgabe `llm-stop signal received.`,
     `llm_stopped = true` persistieren (`save_llm_stopped(true)`; Fehler → stderr
     `Could not persist llm_stopped: <Meldung>`), `check_and_recover()`, nächster Zyklus.
   - llm_start_event gesetzt? → löschen, Ausgabe `llm-start signal received.`,
     `llm_stopped = false` persistieren (Fehlerbehandlung gleich), `check_and_recover()`,
     nächster Zyklus.
   - shutdown_event gesetzt? → Schleife verlassen.
   - `check_and_recover()` (3.5.7).
   - shutdown nochmal prüfen → ggf. verlassen.
   - **Wartezeit**: `check_interval_sec` Iterationen, jede wartet bis 1.0 s auf shutdown_event
     (Abbruch bei Setzung) — garantiert, dass ein Shutdown innerhalb ~1 s erkannt wird,
     statt bis zu `check_interval_sec` (z. B. 30 s) zu blocken.
9. **finally**: `stop(restart_timeout_sec)` (stoppt llama-server graceful), PID-Datei
   löschen, Ausgabe `llama-monitor daemon stopped.`

> Der Daemon läuft **im Vordergrund** (systemd `Type=simple`). Hintergrundstart nur durch
> systemd, `nohup` oder Shell-`&`. Es gibt **keinen** Doppel-Fork.

### 3.6 Health-Check

Konstanten: Endpunkt-Pfad `/health`, erwarteter Status **200**, erwarteter Body exakt
`{"status":"ok"}` (ohne Leerzeichen). Standard-URL `http://127.0.0.1:8080/health`,
Standard-Timeout **3.0 s**.

**Prüflogik** (für synchronen und asynchronen Pfad identisch):
1. HTTP GET auf die URL mit Timeout.
2. Status ≠ erwarteter Status → false.
3. Body **trimmen** (Whitespace an beiden Enden entfernen).
4. true, wenn getrimmter Body == erwarteter Body **oder** JSON-normalisierte Vergleiche
   gleich sind (JSON parsen → mit sortierten Schlüsseln serialisieren, auf beiden Seiten;
   nicht-parsebarer Text vergleicht unverändert — dann ist der direkte Stringvergleich
   schon gefallen, also false).
5. Netzwerk-/Timeout-/Verbindungsfehler jeder Art → false (nie eine Ausnahme nach außen).

**Sync-Wrapper `await_health(url, timeout_sec)`**:
- Versucht, einen bereits laufenden Event-Loop zu übernehmen (Referenz global merken,
  damit der Daemon-Signal-Handler ihn unterbrechen kann); wenn keiner läuft, **neuen**
  Loop erzeugen und die asynchrone Prüfung darin blockierend ausführen.
- Nach dem Durchlauf: selbst erzeugten Loop schließen (referenzieren auf null), den
  übernommenen nicht anfassen.
- Fallback bei "Loop läuft schon" (`RuntimeError`): **synchroner** HTTP-GET mit gleichem
  Timeout und gleicher Prüfung (Status 200, getrimmter Body exakt oder JSON-normalisiert);
  jeder Fehler → false.

> Gotcha: die Loop-Erzeugung hier darf nicht geändert werden, ohne den Daemon-Signal-Handler
> anzupassen — der Handler unterbricht genau diese Loop-Referenz.

### 3.7 HTTP-API (Hermes-Gateway-Integration)

**Server**: threaded HTTP-Server, gebunden an `127.0.0.1:<daemon_port>` (Standard 9500),
läuft in einem Dämon-Thread. Request-Logging ist **vollständig unterdrückt** (keine
stderr-Zeilen des HTTP-Frameworks). Start im Daemon (3.5.8) blockiert in einer
Schleife (0.5 s Poll) bis zum expliziten Stopp; Stopp = Server-`shutdown()` +
`server_close()`, Referenz auf null.

Alle Antworten: JSON, Content-Type `application/json`, Statuscode wie angegeben.
JSON-Serialisierung: kompakt ohne Einrückung (Standard-Optionen).

#### 3.7.1 Endpunkte (exakte Verträge)

| Methode+Pfad | Verhalten | Antwort |
|---|---|---|
| GET `/api/v1/status` | Status zusammensetzen | 200 siehe unten |
| GET `/api/v1/templates` | Template-Namen listen | 200 `{"templates": [{"name": <n>}, …]}` (sortiert) |
| POST `/api/v1/restart` | restart_event setzen | 200 `{"success": true, "message": "Restart initiated"}`; Zustand oder Event fehlt → 500 `{"success": false, "message": "Daemon not running"}` |
| POST `/api/v1/set-template` | Template wechseln | 200 `{"success": true, "message": "Switched to template '<n>' and restart initiated."}`; Fehlerfälle unten |
| POST `/api/v1/llm-stop` | Server gestoppt halten | 200 `{"success": true, "message": "llama-server stop requested; daemon will not restart it."}` |
| POST `/api/v1/llm-start` | Überwachung fortsetzen | 200 `{"success": true, "message": "llama-server start requested; daemon will resume monitoring."}` |
| POST `/api/v1/shutdown` | Daemon herunterfahren | 200 `{"success": true, "message": "Shutdown initiated"}` + Signal-Deliverierung (unten) |
| sonstige GET/POST | — | 404 `{"error": "Not found"}` |

**GET /api/v1/status — exakter Body:**

```json
{
  "pid": <llama_pid oder null>,
  "template": "<aktives Template oder null>",
  "template_name": "<Anzeigename oder null>",
  "model_path": "<Modellpfad oder null>",
  "model_name": "<Dateiname aus model_path, ohne Verzeichnis; null/leer → \"unknown\">",
  "state": "<process_state>",
  "health": "ok" | "unhealthy",
  "config": {"check_interval_sec": <n>, "restart_timeout_sec": <n>},
  "templates_count": <Anzahl Templates>,
  "llm_stopped": <bool>
}
```

(Die Konfiguration wird bei jedem Abruf frisch geladen; `pid` aus dem Daemon-Zustand,
nicht aus der PID-Datei.)

**POST /api/v1/set-template — Fehlerfälle:**

| Bedingung | Status | Body |
|---|---|---|
| Content-Length 0 / kein Body | 400 | `{"success": false, "message": "No template name in request"}` |
| JSON nicht parsebar oder falscher Typ | 400 | `{"success": false, "message": "Invalid JSON body"}` |
| Name leer (weder `template` noch `template_name` Schlüssel enthält einen Wert) | 400 | `{"success": false, "message": "Template name required"}` |
| Template existiert nicht | 400 | `{"success": false, "message": "Template '<n>' not found. Available: <komma-getrennte Liste>"}` |
| Persistieren schlägt fehl | 500 | `{"success": false, "message": "<Ausnahmemeldung>"}` |

Erfolgspfad: `save_active_template(name)` → Zustand `active_template` setzen →
restart_event setzen. Der Name wird aus dem Body-Schlüssel `template` gelesen,
mit Fallback auf `template_name`.

**POST /api/v1/shutdown — Ablauf (kritisch):**
1. shutdown_event setzen; **sofort** 200 antworten (vor der Signal-Deliverierung).
2. Hintergrund-Thread: 0.5 s warten (damit die Antwort rausgeht), dann sich selbst
   (den Daemon-Prozess) SIGTERM senden. Der Signal-Handler (3.5.8) bricht die Schleife auf,
   der finally-Block stoppt den llama-server graceful.
3. Force-Kill-Wachhund-Thread: nach `restart_timeout_sec + 13` Sekunden SIGKILL an den
   eigenen Prozess (Fehler → sofortiger Prozess-Exit mit Code 1).
   **Begründung des Budgets**: der finally-Block stoppt zuerst den llama-server —
   SIGTERM-Wartezeit bis `restart_timeout_sec`, dann SIGKILL-Eskalation mit 5 s Poll.
   `restart_timeout_sec + 13` deckt das volle Graceful-Teardown-Budget plus Reserve ab;
   ein früheres SIGKILL würde den llama-server dauerhaft verwaisten.

**llm-stop/llm-start**: setzen den Zustands-Flag **und** persistieren ihn sofort
(`save_llm_stopped(true/false)`; Persistenzfehler → nur stderr-Warnung
`Warning: could not persist llm_stopped: <Meldung>`, Antwort bleibt 200) und setzen
das jeweilige Ereignis. Der Flag überlebt Daemon-Neustarts (Wiederherstellung in 3.5.8).

### 3.8 CLI-Befehle (exakte Ausgaben, Exit-Codes, Abläufe)

Gemeinsame Helfer (alle Module nutzen sie):

- **Konfigurationspfad**: `<aufgelöstes Home>/.config/llama-monitor/config.json` (Home-Regel 2.3,
  Delegation an die Konfigurationsauflösung — nie eine zweite Implementierung).
- **PID-Datei-Pfad** (CLI): `<aufgelöstes Home>/.config/llama-monitor/llama-monitor.pid`;
  **Fallback**: `/root/.config/llama-monitor/llama-monitor.pid` wird benutzt, wenn die primäre
  Datei fehlt und der Fallback existiert (SUDO_USER-Fall: sudo ließ die Datei unter /root
  schreiben); Lese-/Rechtefehler dabei still.
- **`restart_timeout_sec()`**: aus der Konfiguration lesen (int, Standard 10); jeder Fehler → 10.
  **Zweck**: alle Stoppfad-Budgets hängen daran, weil der Daemon-Signal-Handler zuerst den
  llama-server graceful stoppt (SIGTERM-Wartefenster = restart_timeout_sec, dann
  SIGKILL-Eskalation); wer den Daemon früher killt, verwaist den Server.
- **Service-Datei-Pfad**: `/etc/systemd/system/llama-monitor.service`.

#### 3.8.1 `start` — Daemon im Vordergrund starten

Vor der Ausführung: Elevationsprüfung (3.2). Drei Checks in dieser Reihenfolge:

**Check 1 — läuft schon ein Daemon?**
1. PID-Datei lesen (mit Fallback). Fehlt sie ganz → weiter zu Check 2.
2. Inhalt ist keine Zahl → `Found malformed PID file. Cleaning up.` + Datei löschen → Check 2.
3. Inhalt ist Zahl P: Signal 0 an P senden.
   - Prozess existiert **oder** nur Rechteproblem: Ausgabe
     `Daemon already running (PID <P>). To stop it, run: llama-monitor stop` → **Exit 1**.
   - Prozess existiert nicht: `Found stale PID file (PID <P>). Cleaning up.` + Datei löschen
     → Check 2.

**Check 2 — Konfiguration + Port:** Konfiguration laden; Fehler →
`Error loading config: <Meldung>` (stderr) → Exit 1. `enabled` false →
`Daemon is disabled in config. Set 'enabled': true in config.json` → **Exit 0**.
Port-Prüfung auf `daemon_port` (Standard 9500) via `ss -tlnp` (gleiche Zeilen-Parsing-Regel
wie 3.5.3; zusätzlich `address` = 4. Whitespace-Feld der Zeile oder `"unknown"`):
Konflikt → Ausgaben (Exit 1):
```
ERROR: Daemon port <P> is already in use!
  PID:       <pid>
  Process:   <name>
  Address:   <address>
Stop that process or change the daemon_port in config.json.
```

**Check 3 — Template-Port-Hinweise (nur Warnung):** Für jedes Template (Reihenfolge
`list_template_names`): Template laden (Fehler → überspringen); `port` ≠ daemon_port und Port
belegt → Warnung:
`WARNING: Template '<Anzeigename>' uses port <p>, which is already in use by PID <pid> (<name>). llama-monitor will monitor this instance.`

Danach: `run_daemon(config)` (3.5.8) — blockiert bis Shutdown.

#### 3.8.2 `stop` — Daemon stoppen (kein Root nötig)

**API-Pfad** (`stop_via_api`), benutzt wenn PID-Datei fehlt/unlesbar/malformed:
1. Ausgabe `Shutting down via daemon API...`
2. POST `http://127.0.0.1:9500/api/v1/shutdown` (Timeout 5 s).
   - Verbindungsfehler → stderr `Cannot connect to daemon API.` +
     stderr `Try: sudo systemctl stop llama-monitor` → Exit 1.
   - Sonstiger Fehler → stderr `Shutdown failed: <Meldung>` → Exit 1.
   - Status ≠ 200 → stderr `API error: <status> - <text>` → Exit 1.
3. Ausgabe `Shutdown initiated. Waiting for daemon to stop...`
4. Warteschleife: Budget = `restart_timeout_sec() + 15` Sekunden; alle 0.5 s prüfen, ob die
   API auf `/api/v1/status` nicht mehr antwortet (Verbindungsfehler = tot). API weg →
   `Daemon stopped.` → Exit 0. Budget erschöpft → stderr
   `Daemon did not stop within timeout.` → Exit 1. Die Schleife killt **nie** — ein frühes
   SIGKILL würde den llama-server verwaisten.

**PID-Pfad** (PID lesbar):
1. `os.kill(pid, SIGTERM)`; Ausgabe
   `Sent SIGTERM to daemon (PID <pid>). Waiting for shutdown...`
2. Warten: Grace = `restart_timeout_sec() + 18`; alle 0.25 s Signal 0 testen.
   - Prozess weg innerhalb Grace: `Daemon stopped.`, PID-Datei löschen (fehlertolerant) → Exit 0.
   - Budget erschöpft: stderr `Daemon did not stop gracefully. Sending SIGKILL...`,
     SIGKILL senden, 1 s warten, PID-Datei löschen, `Daemon killed.` (kein Exit — fällt
     durch zum Ende; Exit-Code ist der normale).
3. `ProcessLookupError` (PID tot): `Daemon not running (stale PID file).`,
   PID-Datei löschen → Ende (Exit 0).
4. `PermissionError` (Daemon gehört root): in den API-Pfad verzweigen.

> Budget-Begründung: SIGTERM-Handler des Daemons stoppt zuerst den llama-server (Grace
> restart_timeout_sec + SIGKILL-Eskalation + 5 s Poll + Latenz). CLI-Budget deckt das ab:
> `restart_timeout_sec + 18`. **Die CLI killt den Daemon nur nach Ablauf dieses vollen
> Budgets** — niemals früher.

#### 3.8.3 `restart`

Als root: `stop` → 1 s warten → `start`. **Nicht** als root: `stop` (läuft über API oder
eigenes Signal), 1 s warten, dann **Elevation nur für den Start-Teil** (`elevate(["start"])`)
— nach der Elevation endet dieser Prozess.

#### 3.8.4 `status` (Option `--json`)

Daten sammeln:
1. `daemon_running`: PID-Datei (mit Fallback) lesen; Signal 0 erfolgreich **oder**
   PermissionError → true (Prozess existiert). Parse-/Lese-/Rechtefehler beim Dateizugriff
   → false. Fehlt die Datei → false.
2. `autostart_enabled`: Existenz der Service-Datei.
3. Konfiguration laden (jeder Fehler → leeres Dict); `check_interval_sec`/
   `restart_timeout_sec` daraus, fehlend → String `"unknown"`.
4. Template-Liste + Anzahl.
5. **API-Status** asynchron laden: GET `http://127.0.0.1:9500/api/v1/status`, Timeout 2 s;
   200 → JSON-Objekt; jeder Fehler → null (asynchron, weil es innerhalb eines eventuell
   bestehenden Event-Loops laufen kann).
6. Aktives Template herleiten: **erst** aus dem API-Status (`active_template` gesetzt →
   Anzeigename = `template_name` oder `active_template`, `model_path`, Port/Bind aus der
   API-Antwort mit Standard 8080/127.0.0.1 — die API liefert diese Felder nicht, also
   effektiv die Defaults); **sonst** aus der Konfiguration (Template laden, Fehler still).
   Daraus `llama_server_url` = `http://<bind>:<port>`.
7. `llama_server_url` gesetzt → `/v1/models` abfragen (Timeout 3 s; 200 + `data`-Liste
   nicht leer → `{"model_id": <id des ersten>, "model_name": <Dateiname der id>}`; sonst null).
8. Kein API-Status → direkt laufende llama-server suchen (`ps -eo pid,comm`; für jeden
   lebenden PID die Kommandozeile parsen): erstes lebendes nimmt
   `{"pid", "model_path"|"unknown", "model_name"|"unknown", "binary", "cmdline" (erste 5
   Tokens + "..."), "health": "unknown", "state": "running"}`.
   Modellpfad-Erkennung: Token nach `--model`; sonst erstes Token, das `.gguf` enthält.
9. Basis-Dict: `daemon_running`, `autostart_enabled`, `check_interval_sec`,
   `restart_timeout_sec`, `config_path`, `templates_available` (Liste), `templates_count`.
   API-Status vorhanden → in das Basis-Dict **hineinmergen** (API gewinnt); API-`pid` gesetzt
   → `daemon_running` = true. Sonst direkter llama-Status hineinmergen.
10. `/v1/models`-Daten: Felder `running_model_id` und `running_model_name` ergänzen.

**Ausgabe-Formate:**

`--json`: das Dict als JSON mit Einrückung 2.

Sonst (Reihenfolge exakt):
```
=== llama-monitor Status ===
  Daemon: running|stopped
  Autostart: enabled|disabled
  Check interval: <n>s
  Restart timeout: <n>s
  Config: <pfad>
  Templates available: <n>
    - <name>          (für jedes Template)
<leere Zeile>
  Active template: <name oder N/A>
  Model file: <pfad oder N/A>
  llama-server PID: <pid>          (nur wenn API-Status + pid)
  Process state: <state>           (nur wenn API-Status + pid)
  Health: <health>                 (nur wenn API-Status + pid)
  llm stopped: <bool-String>       (Standard "False" wenn kein API-Status)
<leere Zeile>
  Running model (from /v1/models):
    ID: <id>
```
oder statt der letzten zwei Zeilen (wenn kein `/v1/models`-Ergebnis, aber direkter
Prozessfund):
```
<leere Zeile>
  llama-server process found:
  Model: <name>
  PID: <pid>
  Command line: <cmdline>
```

Exit **0** immer (auch bei fehlendem Daemon).

#### 3.8.5 `list-templates`

JSON-Array aller Template-Namen (sortiert), Einrückung 2. Kein weiterer Text. Exit 0.

#### 3.8.6 `reload-template [name]`

1. Kein Name: `active_template` aus der Konfiguration; auch das fehlt → Ausgaben
   `No template specified and no active template configured.` +
   `Usage: llama-monitor reload-template <template-name>` → Exit 1. Sonst Ausgabe
   `No template specified — using active template: <name>`.
2. POST `/api/v1/set-template` mit Body `{"template": <name>}` (Timeout 5 s):
   - 200 → Ausgabe `Restart initiated: <message aus der Antwort>` → Exit 0.
   - anderer Status → stderr `API error: <status> - <text>` → Exit 1.
   - Verbindungsfehler → stderr `Cannot connect to llama-monitor daemon. Is it running?` +
     stderr `Try: sudo systemctl start llama-monitor` → Exit 1.
   - sonstiger Fehler → stderr `Error: <Meldung>` → Exit 1.

#### 3.8.7 `set`

1. Keine Templates → `No templates available.` → Exit 1.
2. Aktuelles aktives Template aus der Konfiguration (Fehler → null). Ausgaben:
   `Current active template: <name oder (none)>`, Leerzeile, `Available templates:`, dann
   je Template `  <i>. <name> (model: <Dateiname>, port: <p>)` mit Suffix ` ◀ current`
   beim aktiven; Ladefehler → `  <i>. <name> (error: <Meldung>)`.
3. Prompt `Select template number`, Vorauswahl = Index des aktuellen + 1 (wenn vorhanden),
   sonst `"1"`. Nicht-parsebare Eingabe oder außerhalb 1..n →
   `Invalid selection. Use 1-<n>.` → Exit 1.
4. `save_active_template(chosen)`; Ausgabe `\n✓ Active template set to: <chosen>`.

> `set` ändert **nur** die Konfiguration; ein laufender Daemon wird nicht neu gestartet
> (nur `reload-template`/`set-template` löst den Neustart aus).

#### 3.8.8 `enable-autostart` / `disable-autostart` (Option `--sudo`)

**enable-autostart:**
1. Nicht root und kein `--sudo` → stderr exakt:
```
Error: --enable-autostart requires root privileges.
Usage:
  sudo llama-monitor enable-autostart
  llama-monitor enable-autostart --sudo
```
→ Exit 1.
2. Nicht root mit `--sudo` → Re-Exec via `elevate(["enable-autostart"])` (3.2, **derselbe
   Mechanismus wie start/restart**); der elevierte Kindprozess führt den Befehl komplett aus.
3. Als root: Unit-Inhalt exakt (Platzhalter ersetzt):
```
[Unit]
Description=Llama Server Monitor
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/llama-monitor start
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=HOME=<aufgelöstes Nutzer-Home>
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=<aufgelöstes Home>/.config/llama-monitor

[Install]
WantedBy=multi-user.target
```
   nach `/etc/systemd/system/llama-monitor.service` schreiben; Ausgabe
   `Created systemd service: <pfad>`; dann `systemctl daemon-reload` **und**
   `systemctl enable --now llama-monitor` (beide mit Fehler-Check — Fehler bricht ab);
   Ausgabe `llama-monitor autostart enabled and started.`

**disable-autostart:** gleicher Rechte-/Elevationsvertrag (Fehlertext mit
`--disable-autostart`). Als root: `systemctl stop llama-monitor` und
`systemctl disable llama-monitor` (jeweils **ohne** Fehler-Check), dann Unit-Datei löschen
(falls vorhanden) und `systemctl daemon-reload` (ohne Fehler-Check); Ausgabe
`llama-monitor autostart disabled and removed.`

#### 3.8.9 `logs [lines]` (Standard 40)

`journalctl -u llama-monitor -n <lines> --no-pager` (Timeout 10 s, Text). Leere Ausgabe →
`No log entries found.`; sonst Ausgaben:
`=== llama-monitor logs (last <lines>) ===`, Leerzeile, dann Journal-Inhalt.
Timeout → stderr `Error: journalctl timed out.` → Exit 1; fehlendes journalctl → stderr
`Error: journalctl not found. Is systemd running?` → Exit 1.

#### 3.8.10 `llm-stop` / `llm-start`

- `llm-stop`: Ausgabe
  `Stopping llama-server (daemon will not restart it until 'llm-start')...` dann POST
  `/api/v1/llm-stop` (Timeout 5). 200 → Exit 0; sonstiger Status → stderr
  `API error: <status> - <text>` → Exit 1; Verbindungsfehler → stderr
  `Cannot connect to llama-monitor daemon. Is it running?` + stderr
  `Try: sudo systemctl start llama-monitor` → Exit 1.
- `llm-start`: Ausgabe `Starting llama-server (daemon resumes monitoring)...`, sonst
  identisch mit Endpunkt `/api/v1/llm-start`.

#### 3.8.11 Wizard-Grundmechanik (alle interaktiven Befehle)

**Prompt `prompt_input(text, default)`**: Anzeige `"<text> [<default>] "` wenn Default
gesetzt, sonst `"<text> "`; Eingabe trimmen; Leer-/keine Eingabe → Default zurückgeben
(sonst der getrimmte String).

**Prompt `prompt_yes(text, default=false)`**: Anzeige `"<text> (Y/n) "` wenn Default true,
sonst `"<text> (y/N) "`; Eingabe trimmen + kleinschreiben; Leer-Eingabe → Default;
Rückgabe true nur bei `y` oder `yes`.

**Prompt `prompt_yes`-Abbruchverhalten**: Ein Abbruch (EOF/KeyboardInterrupt) propagiert —
der Einstiegspunkt fängt KeyboardInterrupt als Exit 130.

**GGUF-Modell-Liste (`~/models/`)**: existiert nicht → leere Liste; sonst **nur Dateien**
mit Endung `.gguf`, sortiert.

**Template-Binärdatei-Finder**: rekursiv
`~/**/build/bin/llama-server` und `~/.lmstudio/extensions/backends/*/llama-server`
(glob rekursiv; nur reguläre Dateien mit Ausführungsrecht), plus PATH-Suche
(`which llama-server`). Deduplizierung über den **realen** Pfad (Symlinks aufgelöst),
Wert = absoluter Pfad; Rückgabe sortiert.

**Binärauswahl `prompt_binary_selection(default)`**: keine Binaries gefunden → Default
zurückgeben. Default-Anzeige = übergebener Default oder erster Fund. Ausgabe:
`Available llama-server binaries:` (Leerzeile davor), dann je Eintrag
`  <i>. <pfad>` mit Suffix `  (active)` beim Eintrag, der mit dem Default
(`samefile`-Vergleich) übereinstimmt. Prompt `  Binary selection (number)` mit
Vorauswahl = aktiver Index. Zahl 1..n → dieser Pfad; sonst (auch ungültige Zahl) →
übergebener Default.

#### 3.8.12 Wizard-Parameter-Sammlung (`create-base`, `create-from`, `edit` ohne Namen)

`collect_parameters(template, ask_all=None, ask_binary=True)` mutiert das Template in-place
und gibt es zurück:

1. **Binärdatei** (wenn `ask_binary` und Funde): Default = Template-`binary` oder erster
   Fund; Auswahl treffen; gewählter Pfad → `template["binary"]`; Ausgabe `  -> <pfad>`.
2. `ask_all` null → Prompt `  Configure llama-server parameters?` (Default **ja**).
   Antwort nein → sofort Template zurückgeben (nur die Binärauswahl war aktiv).
3. Spezifikationen = Introspektion des Builds (3.8.14) oder, wenn nicht verfügbar, die
   statische Tabelle.
4. **Kernabfrage**: alle Spez mit Schlüssel in `CORE_KEYS` **oder** `always_ask` — in
   Tabellenreihenfolge. **Rest**: alle übrigen.
5. Gruppen-Walk (unten) über die Kernabfrage.
6. Rest: nur Spez, die aktuell aktiv sind **oder** deren Schlüssel bereits im Template
   steht. Wenn übrig: Leerzeile; Prompt
   `  Show <n> additional parameter(s) this build supports?` (Default **nein**); ja →
   Gruppen-Walk über den Rest.
7. `finalize_template` (unten) ausführen; Template zurückgeben.

**Gruppen-Walk `prompt_groups(specs, template, show)`**: über alle Gruppen in
Tabellen-Reihenfolge; je Gruppe: anwendbare Spez (aktiv laut Template); keine → überspringen.
Ausgabe `\n=== <gruppe> (<n> parameter[s]) ===` (Singular ohne `s` bei n=1); Prompt
`  Configure <gruppe>?` (Default **ja**); nein → Ausgabe `  (unchanged)`, Gruppe fertig;
ja → `show(group_specs)`, das **vor jedem einzelnen Prompt** die Aktivität erneut prüft
(welche Gruppengrenzen überschreitet: ein mid-group gesetzter Wert kann spätere
Parameter derselben Gruppe freischalten) — inaktive Spez werden ausgelassen; für aktive:
Aktueller Wert = Template-Wert oder Spec-Default; `prompt_spec` fragen; Rückgabe nicht-null
→ `template[key] = value`. **null** (Leer-Eingabe bei freiem Prompt) → Wert unverändert.

**`prompt_spec(spec, current)`** (Label = Template-Schlüssel, Anzeige `<schlüssel> <flag>`):
- `select` mit Optionen → nummerierte Auswahl (unten).
- `bool`: Ausgabe `  <schlüssel> <flag>: on|off` plus, wenn kein off_flag existiert und der
  llama-server-Default wahr ist, Hinweis `; no --no- flag in this build` (mit zwei
  Leerzeichen abgetrennt); Prompt `    <schlüssel>?` (Default = aktueller Wert als bool);
  ja → true, nein → false (immer true/false zurückgeben).
- `int`/`float`/`str`/sonstige: Anzeige `  <schlüssel> <flag>[  (<help>)] [<typwort>]`
  (typwort: integer/number/text bzw. "value"); Prompt mit Default = String des aktuellen
  Werts (null bleibt ohne Default-Anzeige); Leer-Eingabe → aktuellen Wert behalten;
  sonst Typ-Koercion (unten).

**Nummerierte Auswahl `prompt_select(spec, current)`**:
1. Optionen = Kopie der Optionsliste; `custom_index` = n+1.
2. Aktueller Index: erster Optionswert, der dem aktuellen gleicht
   (Gleichheit: null==null, beide falsy gleich, sonst Stringvergleich).
3. Ausgabe `\n  <schlüssel> <flag>[  (<help>)]  [current: <anzeige>]`; Anzeige: leer/null
   → `(unset)`, sonst String. Darunter die nummerierte Liste: Einträge
   `    <i>. <anzeigetext>` mit Suffix ` ◀` am aktuellen Index; lange Listen (>12 Einträge)
   **spaltenweise** (6 Spalten, Zellbreite = längste Zelle + 2, jede Zeile rechts getrimmt);
   letzte Zeile: `    <custom_index>. custom value...`.
4. Prompt `    Selection` (Vorauswahl = aktueller Index, falls >0, sonst ohne Default).
   - Leere Eingabe/null → aktuellen Wert behalten.
   - Keine Zahl → als Custom-Wert behandeln (Koercion unten).
   - Zahl == custom_index → Prompt `    Custom <schlüssel> value`; leer/null → aktuellen
     Wert behalten; sonst Koercion.
   - Zahl 1..n → Optionswert an diesem Index (genau dieser Wert, ohne Koercion).
   - Zahl außerhalb: gilt als **Custom-Wert, nicht als Tippfehler** — Prompt
     `    Custom <schlüssel> value (1-<custom_index>, or a value)`; leer/null → Ausgabe
     `    Invalid selection. Use 1-<custom_index>.` + aktuellen Wert behalten; sonst Koercion.

**Koercion `coerce(kind, raw, fallback)`**: int → int(raw); float → float(raw); bool →
wahr, wenn getrimmt-klein in ("on","true","1","yes","y"); sonst roher String. Parsefehler:
Prompt `    Not a valid <kind>[ [<fallback>]], try again`; leer/null → fallback; sonst
erneut (Endlosschleife bis gültig oder Abbruch).

**Select-Koercion `coerce_select(spec, raw, fallback)`**: Typ aus den Optionen ableiten —
gibt es einen int-Optionswert, wird int gewählt, **außer** es gibt auch float-Optionswerte,
dann float; sonst der Spec-Kind. Damit landet eine getippte `262144` bei `ctx_size` als Zahl,
nicht als String. Dann `coerce`.

**Finalisierung `finalize_template(template, …)`**:
1. **Stale-Keys entfernen**: alle Spez-Schlüssel im Template, deren `when`-Prädikat falsch
   ist, löschen; wenn welche entfernt wurden, Ausgabe
   `\nRemoved <n> inapplicable parameter(s): <komma-getrennt, in Tabellenreihenfolge>`.
2. **Legacy-Migration**: `chat_format` aus dem Template nehmen; wenn nicht-leer **und**
   `chat_template` nicht gesetzt → `chat_template = alter_wert` + Ausgabe
   `Migrated chat_format -> chat_template (<wert>)`.
3. **Default-Pruning anbieten**: Kandidaten = alle Template-Schlüssel, die (a) nicht in
   `ALWAYS_KEEP` = {`port`, `bind`, `name`, `model_path`, `binary`} liegen, (b) in der
   Flag-Abbildungstabelle stehen und (c) laut `is_default` den llama-server-Default haben.
   Keine Kandidaten → fertig. Sonst Prompt
   `  Drop <n> parameter(s) left at llama-server defaults?` (Default **ja**); ja → alle
   löschen + Ausgabe `  -> <komma-getrennt, alphabetisch sortiert>`.

#### 3.8.13 `create-base`

1. `base.json` lesen (fehlend → `Base template not found: <pfad>` → Exit 1).
2. Modelle listen; leer → `No GGUF model files found in ~/models/` → Exit 1. Ausgabe
   `\nAvailable models:` + je Eintrag `  <i>. <dateiname> (<größe_mb auf ganze MB> MB)`.
   Prompt `Select model number` (Default `"1"`); ungültig →
   `Invalid selection. Use 1-<n>.` → Exit 1.
3. Namensvorschlag: Dateiname ohne Endung, kleingeschrieben, `-`/`_`/`.` entfernt.
   Prompt `Template name` (Default = Vorschlag).
4. Ausgaben `\nTemplate: <name>`, `Parameter prompts (Enter to accept default):`,
   Leerzeile. Template = Kopie von base.json + `model_path` (als String) + `name`.
5. `collect_parameters` (Binärauswahl aktiv).
6. Speichern: `<templates>/<name>.json`, JSON mit Einrückung 4. Ausgaben:
   `\n✓ Template saved: <pfad>`, `  Model: <dateiname>`,
   `  To activate: llama-monitor reload-template <name>`.

#### 3.8.14 `create-from [name]`

1. Keine Templates → `No existing templates to copy from.` → Exit 1.
2. Quell-Auswahl ohne Positionsargument: Ausgabe
   `\nAvailable templates to copy from:` + je Eintrag
   `  <i>. <name> (model: <dateiname>, port: <p>)` (Ladefehler →
   `  <i>. <name> (error: <Meldung>)`); Prompt `Select source template number`
   (Default `"1"`); ungültig → `Invalid selection. Use 1-<n>.` → Exit 1. Mit Argument:
   Name muss existieren → sonst `Error: Template '<n>' not found.` +
   `Available templates: <komma-getrennt>` → Exit 1.
3. Ausgabe `\nBase template: <name>`.
4. Modellwahl wie 3.8.13 (Verzeichnis fehlt → `No ~/models/ directory found.` → Exit 1;
   leer → `No GGUF model files found in ~/models/.` → Exit 1).
5. Namensvorschlag wie oben; Prompt `New template name`.
6. `collect_parameters` auf der **geladenen Quellvorlage** (Binärauswahl aktiv); dann
   `model_path` überschreiben und das Feld `name` **löschen**.
7. Speichern + Ausgaben wie oben, zusätzlich `  From: <quellname>`.

#### 3.8.15 `delete [name]`

1. Keine Templates → `No templates available to delete.` → Exit 1.
2. Ohne Argument: Ausgabe `\nAvailable templates:` + Einträge (wie oben);
   Prompt `Select template number to delete` (**ohne** Default); ungültig/leer →
   `No selection or invalid selection. Aborting.` → Exit 1. Mit Argument: Name muss
   existieren, sonst `Error: Template '<n>' not found.` + `Available templates: …` → Exit 1.
3. Ausgabe `\nTemplate to delete: <name>`; Template laden (Fehler →
   `  (could not read template details: <Meldung>)`); Erfolg →
   `  Model: <pfad>` und `  Port: <p>`.
4. Aktives Template bestimmen: erst Daemon-Zustand (falls verfügbar), sonst Konfiguration
   (Fehler → null). Wenn aktiv: Ausgaben
   `\n⚠ WARNING: This is the ACTIVE template.`, `  Deleting it will leave llama-monitor with no active template.`,
   `  Activate another template first, then delete.`; Prompt `Delete anyway?` (Default nein);
   nein → `Aborted.` → Exit 0.
5. Prompt `Delete template '<name>'?` (Default nein); nein → `Aborted.` → Exit 0.
6. Datei muss existieren → sonst `Error: Template file not found: <pfad>` → Exit 1;
   löschen; Ausgabe `\n✓ Template deleted: <pfad>`; I/O-Fehler → stderr
   `Error deleting template: <Meldung>` → Exit 1.
7. **Modell-Datei anbieten** (`maybe_delete_model`): übersprungen, wenn Template-Details
   fehlen oder `model_path` fehlt oder die Datei (mit `~`-Expansion) nicht existiert.
   Wenn ein **anderes verbleibendes Template** denselben Modellpfad referenziert →
   `Model file kept — still used by template '<name>': <pfad>` und fertig. Sonst: Dateigröße
   (I/O-Fehler → 0 MB) und Ausgabe `\nModel file: <pfad> (<n> MB)`; Prompt
   `Also delete the model file?` (Default nein); nein → `Model file kept.`; ja → löschen,
   Ausgabe `✓ Model deleted: <pfad>`; I/O-Fehler → stderr
   `Error deleting model file: <Meldung>` (kein Exit).

#### 3.8.16 `models`

1. Keine GGUF-Dateien → `No GGUF files found in ~/models/` → Ende (Exit 0).
2. Template-Modellpfade sammeln (Basisnamen; Ladefehler überspringen). Ausgabe
   `\nModels without templates:`; je Modell ohne Template
   `  - <name> (<n> MB)`.
3. Keine fehlenden → `  All models already have templates.`; sonst
   `\n  <n> model(s) without template.`

#### 3.8.17 `edit [parameter]`

1. Keine Templates → `No templates to edit.` → Exit 1.
2. **Immer** zuerst Template-Auswahl (`select_template("edit")`): Ausgabe
   `\nAvailable templates:` + Einträge (wie oben); Prompt `Select template to edit`
   (Default `"1"`); nicht-parsebare Eingabe → `Aborted.` → null; außerhalb des Bereichs →
   `Invalid selection. Use 1-<n>.` → null; null → Exit 1.
3. Template laden; Ausgabe `\nEditing: <name>`; wenn Feld `name` gesetzt:
   `  Model: <name>`.
4. **Mit Parametername**: Einzel-Edit (unten). Keine Änderung → `\nNothing changed.` +
   Ende (kein Speichern). Änderung → speichern (`save_template`, unten).
5. **Ohne Namen**: `collect_parameters(template, ask_all=true)` (Binärauswahl aktiv),
   dann speichern.

**`save_template`**: JSON mit Einrückung 4 in `<templates>/<name>.json`; Ausgaben
`\n✓ Template saved: <pfad>` und `  To apply it: llama-monitor reload-template <name>`.

**Parameter-Auflösung `resolve_parameter(name) → (spec|null, hinweistext|null)`** —
vier Strategien in dieser Reihenfolge (Eingabe erst trimmen, dann führende `-` entfernen;
Präfix `no-` abschneiden — akzeptiert `--no-jinja` als `jinja`):
1. Genaue Übereinstimmung: `roh` und `roh` mit `-`→`_` gegen die **Build-spezifischen**
   (introspektierten) Schlüssel. Fund → (spec, null). Wenn der Kandidat nur in der
   **statischen** Tabelle liegt → (null, `Parameter '<k>' (<flag>) is not supported by the selected llama-server build.`).
2. Trennzeichen-frei: beide Argumente von `_`/`-` befreien (`cachetypek` == `cache-type-k`);
   gleiche Reihenfolge: Build-Schlüssel → Fund; statischer Schlüssel → Build-Hinweis (wie oben).
3. Flag-Namensvergleich: Spez aus der statischen Tabelle, deren `flag` == Originalname oder
   == `--<roh>`: spec = Build-Spez falls vorhanden; Hinweis nur, wenn der Schlüssel im
   Build fehlt (Text wie Strategie 1, mit dem echten Schlüssel).
4. Sonst (null, `Unknown parameter.`) — plus, wenn `_close_keys` Treffer liefert:
   ` Did you mean: <bis zu 8 Treffer, komma-getrennt>?`

**Vorschlagslogik `_close_keys(key, available)`** (Eingabe kleingeschrieben, Trenner
entfernt; leer → keine Vorschläge; Vergleichsbasis: Schlüssel kleingeschrieben ohne `_`):
1. **Substring**: alle Build-Schlüssel, die die Nadel enthalten → sofort zurückgeben.
2. **Subsequenz**: alle Build-Schlüssel, die mit den **ersten zwei Zeichen** der Nadel
   beginnen **und** die Nadel als Teilfolge enthalten (die 2-Zeichen-Präfix-Regel unterdrückt
   das Rauschen von `ctxt` → alle `ctx_*`).
3. Letzter Ausweg: Distanz-Kandidaten (`difflib.get_close_matches`) mit **n=3, cutoff 0.7**.

**Einzel-Edit `edit_single_parameter(template, name) → bool`**:
1. Auflösung; spec null → Hinweis mit zwei Leerzeichen eingerückt ausgeben (`  <hinweis>`)
   → false.
2. **Parameter inaktiv** → Voraussetzungs-Dialog (unten); Abbruch → `  Aborted.` → false;
   keine Änderung → false.
3. Ausgabe `\n<schlüssel> <flag>[ [not set; llama-server default: <anzeige>]]` (Hinweis
   nur wenn der Schlüssel noch nicht im Template steht; Anzeige leer/null → `(unset)`).
4. `prompt_spec` fragen.
5. Wert != aktueller Wert (Gleichheitsregel oben) → `template[key] = value`, Ausgabe
   `  -> <key> = <repr>`, true; wenn der neue Wert ein llama-server-Default ist: Zusatzausgabe
   `  Note: <repr> is llama-server's default for <key>.`
6. Wert unverändert, Schlüssel vorhanden → als Alternative zum Editieren das Entfernen
   anbieten: Prompt `  Remove <key> from the template?` (Default nein); ja → Schlüssel
   löschen, Ausgabe `  -> removed <key>`, true.
7. Wert unverändert, Schlüssel fehlt, gewählter Wert == llama-server-Default → explizites
   Pinning anbieten: Prompt `  Store <key> anyway (it equals the llama-server default)?`
   (Default nein); ja → setzen wie in 5 (mit derselben Ausgabe, ohne die Note), true.

**Voraussetzungs-Dialog `prompt_prerequisites(spec, template, tiefe=0)`**:
1. Gate-Schlüssel ermitteln (das `when`-Prädikat ausführen und jeden Lesezugriff
   aufzeichnen — inkl. impliziter Lesezugriffe durch `t.get(...)`, die der Prädikat-Text
   selbst verrät). Ausgabe `\n<key> (<flag>) does not apply to this template[ because <k>=<repr>, …]`
   (Begründung nur wenn es gelesene Schlüssel gibt; Repr der aktuellen Werte per
   Python-repr-Konvention, fehlend = leerer String in Anführungszeichen).
2. Für jeden Gate-Schlüssel mit bekannter Spec: aktuellen Wert anzeigen
   (`  <key> is currently '<anzeige>'` — Anzeige per `(unset)`-Regel, in einfache
   Anführungszeichen), `prompt_spec` fragen; Wert unverändert/null → überspringen; sonst
   setzen + `  -> <key> = <repr>` + Änderungsliste.
3. Ist der Parameter jetzt aktiv **oder** gab es keine Gate-Schlüssel → Änderungsliste
   zurückgeben.
4. Sonst: wenn Tiefe < 3, für jeden **selbst inaktiven** Gate-Schlüssel rekursiv
   denselben Dialog (Tiefe+1) — die Kette wird durch Ausprobieren aufgelöst, nicht
   hartkodiert.
5. Immer noch inaktiv → Ausgabe `  <key> still does not apply with these values.` und
   **Abbruch-Ausnahme** werfen.

> Ein Abbruch in jedem Tiefe-Level bricht die Kette komplett ab (Ausnahme propagiert bis
> `edit_single_parameter`, das `  Aborted.` ausgibt und false zurückgibt).

#### 3.8.18 Build-Introspektion (`runtime_specs`)

Zweck: die statische Tabelle an die tatsächliche llama-server-Binärdatei anpassen.

1. **Hilfekonserve**: `<binary> --help` einmal ausführen (Timeout 10 s, stdout fangen);
   Ergebnis pro Binärpfad cachen (auch Fehler → leerer String cachen, kein wiederholter
   Aufruf).
2. Keine Binärdatei bekannt oder Hilfe leer/nur-Whitespace → **statische Tabelle**
   zurückgeben.
3. Filter: Spez behalten, deren `flag` als Teilstring im Hilfetext vorkommt; andere
   verwerfen ("nicht von diesem Build unterstützt").
4. **Select-Listen auffrischen**: Werte aus dem Hilfeeintrag des Flags extrahieren.
   Extraktionsregeln `_help_flag_values` — Blockfindung zuerst: die erste Hilfezeile, die
   das Flag als eigenständiges Wort enthält (Grenzen: keine Wort-/Bindestrich-Zeichen
   direkt davor oder danach, damit `--port` nicht `--parallel` matcht), plus alle
   **eingerückten** Folgezeilen (Einrückung ≥ 8 Leerzeichen oder Tab; Stop bei Leerzeile
   oder nicht-eingerückter Zeile). Dann vier Formate in dieser Reihenfolge:
   a. **Inline**: `[a|b|c]` oder `{a,b,c}` in der **ersten** Blockzeile; splitten an
      `|` und `,`; gültige Liste → zurückgeben.
   b. **Mehrzeilig `allowed values:`** (Groß-/Kleinschreibung egal): Fortsetzungszeilen
      mit ≥20 Zeichen Einrückung dürfen miteinander verschmolzen werden; alles ab
      `(default` abschneiden; an Kommas splitten; gültig → zurückgeben.
   c. **Aufzählung** `- value: description`: alle passenden Werte sammeln (Zeichenklasse:
      Buchstabe/Ziffer + `._+-`); mindestens 2 und gültige Liste → zurückgeben.
   d. **Bare**: direkt nach dem Flag eine kommagetrennte Kleinschreibungs-Liste
      (`a,b,c`, mindestens 2 Elemente); gültig → zurückgeben.
   **Gültigkeitsprüfung `_is_value_list`**: mindestens ein Wert; **alle** Werte matchen
   `^[A-Za-z0-9][A-Za-z0-9._+-]*$`; und nicht (genau 1 Wert, der klein ein Platzhalter ist:
   n, type, mode, index, value, num, str, int). Ziel: Platzhalter wie `N`/`TYPE`/`MODE`
   und Prosa abweisen.
5. **Merge-Regel** für frisch extrahierte Listen: die Build-Liste ist **autoritativ**;
   vorangestellt bleibt der statische "unset"-Eintrag (Optionswert `""`), hinten angehängt
   wird der llama-server-Default (falls nicht null) — auch wenn `--help` ihn nur in der
   Default-Notiz dokumentiert. Deduplizierung nach String-Wert, Erster gewinnt.
   Sonderregel `split_mode`: den Wert `tensor` aus der Build-Liste entfernen
   (experimentell, wird nicht angeboten).
6. **off_flag-Drop**: boolean Spez mit `--no-x`, das im Hilfetext **nicht** vorkommt →
   off_flag entfernen (die Verneinung kann nicht emittiert werden).
7. Spez mit Änderungen → **Kopie** mit nur diesen geänderten Feldern; unveränderte Spez
   bleiben dieselben Objekte. Reihenfolge der Tabelle bleibt erhalten.

> Wenn Introspektion fehlschlägt (Binary nicht ausführbar, keine Hilfe), wird die
> vollständige statische Tabelle benutzt — der Wizard bleibt immer benutzbar.

### 3.9 Prozessmonitor

**Prozessstatus-Objekt**: `pid`, `alive` (bool), `zombie` (bool), `status` (String:
`running`/`sleeping`/`zombie`/`stopped`/`dead` oder das rohe ps-Zeichen).

**`get_process_state(pid) → Prozessstatus`**:
1. `pid ≤ 0` → tot (alive=false, zombie=false, status="dead").
2. Signal 0 senden:
   - `ProcessLookupError` → tot.
   - `PermissionError` → Prozess existiert (anderer Nutzer) → ps-Pfad.
3. **ps-Pfad** (`ps -p <pid> -o stat= --no-headers`, Timeout 5 s):
   - Exit ≠ 0 oder keine Ausgabe → tot.
   - Status-Zeichen = **erstes Zeichen** des getrimmten Outputs:
     `Z` → zombie (alive=true); `R` → running; `S` → sleeping; `T` → stopped;
     alles andere → alive mit rohem Zeichen als Status.
   - Timeout/fehlendes ps → tot.

**`is_process_zombie(pid)`**: Komfort über `get_process_state().zombie`.

**`_terminated(state)`**: true, wenn nicht alive **oder** zombie. Begründung: nach SIGTERM
kann das Kind als Zombie zurückbleiben, bis der Elternteil es reapet; ein Zombie als
"lebendig" zu behandeln verbrennt das SIGTERM→SIGKILL-Budget auf einem längst
untergegangenen Prozess und wiederholtes SIGKILL auf ihn ist wirkungslos. Reapen bleibt
Sache des Erzeugers (nur der Besitzer des Prozess-Handles).

**`kill_process_graceful(pid, timeout_sec=10) → bool`**:
1. Status holen; bereits beendet (Zombie/tot) → true.
2. SIGTERM senden; Wartefenster: bis `timeout_sec` lang alle **0.5 s** Status prüfen;
   beendet → true.
3. Weiter lebend: **SIGKILL** senden; Wartefenster: bis **5 s** lang alle **0.25 s**
   prüfen; beendet → true.
4. Nach SIGKILL noch lebend → false.
5. `ProcessLookupError`/`PermissionError` beim Senden → true (weg bzw. nicht killbar = effektiv tot);
   sonstiger OS-Fehler → Warnung `Warning: Error killing process <pid>: <meldung>` → false.

**`find_llama_server_pids() → Liste[int]`**: `ps -eo pid,comm --no-headers` (Timeout 5 s);
jede nicht-leere Zeile: mindestens 2 Felder und Feld 2 enthält `llama-server` → PID
(int-parsen; Parsefehler → überspringen). Fehler/Timeout → leere Liste.

### 3.10 Home-Verzeichnis-Auflösung (eine Quelle, alle Module delegieren)

1. Umgebungsvariable `SUDO_USER` gesetzt → Home = `/home/<SUDO_USER>`.
2. Sonst `~` auflösen; Ergebnis == `/root` → `/home/*` scannen (erste Reihenfolge des
   Dateisystems), das erste Verzeichnis mit `.config/llama-monitor` als Home nehmen;
   kein Treffer → `/root` lassen.
3. Sonst die normale `~`-Expansion.

> Alle drei Konsumenten (Konfigurationspfade, Daemon-PID-Datei, CLI-Pfad-/Service-Erzeugung)
> **delegieren** an diese eine Regel. Grund: unter pkexec wird `SUDO_USER` gelöscht und
> `HOME=/root` gesetzt — nur die `/home/*`-Suche findet das echte Nutzer-Home; PID-Datei und
> systemd-Unit müssen aber im Nutzer-Home landen, sonst findet die unprivilegierte CLI den
> Daemon nie.

### 3.11 Fehlerbehandlung & Fehlermeldungs-Katalog (exakte Texte)

Alle fatale Fehler im Einstiegspunkt: unbehandelte Ausnahme → `Error: <meldung>` (stderr)
→ Exit 1; `KeyboardInterrupt` → Exit 130. Modulspezifische Meldungen wurden in ihren
Abschnitten exakt dokumentiert (2.3 Konfiguration, 2.4 Template, 3.5 Daemon,
3.7 API, 3.8 CLI). Ergänzend:

| Kontext | Text | Kanal | Exit |
|---|---|---|---|
| Daemon.start: Template-Ladefehler | `Error loading template '<name>': <meldung>` | stderr | — (false) |
| Daemon.start: Binary fehlt | `Binary not found: <pfad>` | stderr | — (false) |
| Daemon.start: Startfehler | `Failed to start llama-server: <meldung>` | stderr | — (false) |
| Daemon.stop: Kill fehlgeschlagen | `WARNING: Could not stop llama-server (PID <pid>)` | stderr | — |
| Prozessmonitor: Kill-Fehler | `Warning: Error killing process <pid>: <meldung>` | stdout | — |
| API: Persistenzwarnung | `Warning: could not persist llm_stopped: <meldung>` | stderr | — (200 bleibt) |
| Daemon-Schleife: Persistenzfehler | `Could not persist llm_stopped: <meldung>` | stderr | — |
| start: Config-Fehler | `Error loading config: <meldung>` | stderr | 1 |
| stop (API): nicht erreichbar | `Cannot connect to daemon API.` + `Try: sudo systemctl stop llama-monitor` | stderr | 1 |
| llm-*/reload-template: nicht erreichbar | `Cannot connect to llama-monitor daemon. Is it running?` + `Try: sudo systemctl start llama-monitor` | stderr | 1 |
| Elevation: kein Werkzeug | `Error: need root privileges but neither pkexec nor sudo is available.` | stderr | 1 |
| pkexec 126/127 | `pkexec could not authenticate (no dialog agent?). Falling back to sudo.` | stderr | → sudo |

**Konvention**: informativ/nicht-fatale Meldungen auf stdout; Fehler auf stderr;
Länge/Format der Meldungen sind Teil des Vertrags.

### 3.12 Edge Cases & Sonderfälle (muss-verhalten)

1. **Stale PID-Datei** mit PID eines fremden/lebenden Prozesses: `start` bricht ab;
   `stop` sendet SIGTERM an die (falsche) PID — Risiko akzeptiert, Meldung indentisch.
2. **Port 8080 belegt von Fremdserver, autostart aktiv**: Daemon adoptiert (3.5.8),
   `server_owned=false`; Health-Check übernimmt die Kontrolle; erst bei Health-Fehler wird
   übernommen (kill + restart mit force_kill).
3. **Template-Port == daemon_port**: Check 3 überspringt den Konflikt-Warnvergleich.
4. **LLM gestoppt + Neustart des Daemons**: `llm_stopped=true` überlebt (Persistenz);
   Daemon startet **nicht** automatisch den Server wieder (3.5.7 Schritt 1).
5. **set-template auf aktives Template**: erlaubt; führt denselben Restart aus.
6. **select mit Leerstring-Option ("unset")**: Wert `""` wird vom Daemon **nicht** emittiert
   (3.4 Schritt 5); der Schlüssel darf trotzdem im Template stehen.
7. **Bool falsch ohne off_flag im Build**: nichts emittieren (Abwesenheit = Verneinung).
8. **Ganze Zahl-Portierung**: alle Zahlenwerte landen als String im Argument; Floats mit
   nativer Repräsentation (`0.8`, `0.1`, `1.75`).
9. **Port-Scan-Parser-Nebeneigenschaft**: `:8080` matcht auch `:80801`-Zeilen nicht
   (Trennzeichenpflicht nach dem Port).
10. **`/home/*`-Scan bei mehreren Nutzern**: erster Treffer gewinnt (nicht deterministisch
    zwischen Installationen, aber innerhalb einer Installation stabil).
11. **journald nicht installiert / systemd absent**: `logs` fehlschlägt mit klarer Meldung;
    `enable-autostart` bricht beim systemctl-Aufruf mit Exit ≠ 0 ab.
12. **Bereits laufender llama-server beim Daemon-Start ohne autostart-Flag**: der erste
    Zyklus von `check_and_recover` adoptiert ihn trotzdem (Schritt 6 im Entscheidungsbaum).
13. **KeyboardInterrupt im Daemon**: SIGINT-Handler == SIGTERM-Handler (graceful).
14. **Doppelte `--model`-Tokens in externer Kommandozeile**: das letzte Vorkommen gewinnt
    (Parser überschreibt).
15. **status ohne Daemon**: funktioniert trotzdem (direkter Prozessfund, Konfig-Werte);
    fehlende Werte erscheinen als "N/A"/"unknown"/"False"-Strings gemäß 3.8.4.

### 3.13 Zustandsmanagement & Persistenz (Übersicht)

| Zustand | Träger | Lebensdauer | Persistiert? |
|---|---|---|---|
| Daemon-Zustand (pid, template, health…) | Prozess-Singleton | bis Daemon-Ende | nein |
| llm_stopped | Konfiguration | dauerhaft | **ja** (`save_llm_stopped`) |
| active_template | Konfiguration + Daemon-Zustand | dauerhaft | **ja** (`save_active_template`) |
| PID des Daemons | PID-Datei | bis Stopp | Datei |
| Templates | JSON-Dateien | dauerhaft | Dateien |
| Hilfefonserve | Prozess-Cache | bis Prozess-Ende | nein |
| Loop-Referenz (Health) | Modul-Global | bis Loop-Schluss | nein |

**Seiteneffekte-Zusammenfassung**: Datei-Schreiben (config, templates, PID, Unit),
Prozesse starten/signalisieren, HTTP-Requests (health, /v1/models, API), systemd-Aufrufe,
stdout/stderr-Druck (journald übernimmt). Keine anderen externen Effekte.

### 3.14 Performance-Anforderungen

- Zyklus-Overhead: Health-Check Timeout 3 s; Port-/Prozess-Scans Timeout 5 s;
  API-Statusabfrage Timeout 2 s; `--help`-Aufruf Timeout 10 s.
- Reaktionszeiten (verbindlich): Shutdown-Erkennung ≤ ~1 s (1-s-Poll), API-Force-Kill
  nach `restart_timeout_sec + 13` s, CLI-stop-Grace `restart_timeout_sec + 18` s,
  API-stop-Wartefenster `restart_timeout_sec + 15` s, Crash-Sonde 5 s (0.25 s Poll),
  Reap-Timeout 3 s, Signal-Deliverierung nach shutdown 0.5 s.
- Speicher/Prozesse: konstante Thread-Anzahl (Hauptthread + API-Thread + 1 Log-Reader
  pro Kind); Logs werden zeilenweise gestreamt (kein Puffer-Rückstau).
- Keine anderen O()-Anforderungen; Vorgabe: die Schleife darf bei leisem Betrieb
  vernachlässigbare CPU ziehen (1-s-Wartepoll, keine Busy-Loops).

## 4. Test-Spezifikation (100 % Abdeckung)

### 4.1 Test-Infrastruktur (Fixtures)

**`isolated_config`** (tmp_path, monkeypatch): erzeugt `tmp/.config/llama-monitor/templates/`,
patcht die Modulpfad-Variablen (CONFIG_DIR/CONFIG_FILE/TEMPLATES_DIR) **auf dem
Konfigurationsmodul**, schreibt eine gültige Mindest-Konfiguration
(`enabled=true`, `check_interval_sec=10`, `default_binary=/bin/echo`) und gibt das
Konfigurationsverzeichnis zurück.

**`isolated_template_dir`**: gleiche Pfade ohne config.json; gibt
`(templates_dir, config_dir)` zurück.

**`fake_binary`**: ausführbares Shell-Skript `fake-llama-server` (echo), Pfad als String.

**Test-Grundsätze**: Tests brauchen **keinen** laufenden Daemon oder llama-server; alle
interaktiven Prompts werden durch injizierte Antwortlisten oder regex-basierte
Prompt-Regeln ersetzt; reale Kindprozesse nur dort, wo Prozess-Semantik (Zombie/Reap)
getestet wird. Tests isolieren Pfade **immer über das Konfigurationsmodul** (eine Quelle).

### 4.2 Konfigurationstests (test_config.py, 21 Tests)

**TestLoadConfig (8)**:

| Test | Eingabe | Erwartung |
|---|---|---|
| load_valid_config | Fixture-Config | alle Felder geladen: enabled=true, autostart=false, check_interval_sec=10, restart_timeout_sec=10, daemon_port=9500, default_binary=resolve(/bin/echo) |
| config_defaults | Fixture-Config | optionale Felder haben die Standardwerte (autostart=false, restart_timeout_sec=10, daemon_port=9500) |
| missing_config_file | kein config.json | ConfigError mit `Config file not found` |
| missing_required_field | nur `enabled` | ConfigError mit `check_interval_sec` (Meldung nennt den fehlenden Schlüssel) |
| invalid_binary_path | default_binary=/nonexistent/binary | ConfigError mit `not found` |
| invalid_check_interval | check_interval_sec=0 | ConfigError mit `'check_interval_sec'` |
| invalid_enabled_type | enabled="yes" | ConfigError mit `'enabled'` |
| custom_values | alle Optional-Felder gesetzt (autostart, restart 15, port 9999, log_file ~/logs/…) | alle geladen; log_file ~-expandiert + absolut aufgelöst |
| config_enabled_false | enabled=false | enabled==false geladen (kein Validierungsfehler) |

**TestValidateTemplate (8)**:

| Test | Eingabe | Erwartung |
|---|---|---|
| valid_template | name, model_path=/dev/null, binary=/bin/echo, port 18080, ctx_size | validiert ohne Fehler; name bleibt erhalten |
| missing_model_path | ohne model_path | ConfigError mit `'model_path'` |
| nonexistent_model_file | model_path=/nonexistent/model.gguf | ConfigError mit `not found` |
| invalid_port | port=99999 | ConfigError mit `Invalid port` |
| port_zero | port=0 | ConfigError mit `Invalid port` (0 ist ungültig) |
| template_optional_fields | nur name+model_path+binary | gültig |
| template_without_binary_uses_fallback | ohne binary | gültig (kein Binary-Pflichtfeld im Template) |
| nonexistent_template_binary | binary=/nonexistent/binary | ConfigError mit `not found` |
| template_without_name_field | ohne name | gültig; validate_template setzt **keinen** Namen (load_template tut es) |

**TestListTemplates (3)**: leeres Verzeichnis → `[]`; drei Templates → **sortierte** Liste;
*.txt im Verzeichnis wird ignoriert (nur `.json`-Stems).

**TestLoadTemplate (2)**: gültiges Template laden (name/model_path/binary/**aufgelöst**/port
erhalten); nicht existierender Name → ConfigError mit `not found`.

**TestSaveActiveTemplate (3)**: existierendes Template → active_template gesetzt; nicht
existierendes → ConfigError mit `not found`; andere Felder bleiben erhalten.

**TestActiveTemplateValidation (5)**: load mit aktivem Template ok; ohne → `""`;
mit nicht existierendem → ConfigError `not found`; save schreibt in bestehende Config ohne
Feldverlust; zweimal save überschreibt (letzter gewinnt).

**TestPathExpansion (3)**: `~/test/path` expandiert + absolut, beginnt nie mit `~/`;
absolute Pfade unverändert; relative Pfade relativ zum aktuellen Verzeichnis aufgelöst.

### 4.3 Daemon-Argument-Emission (test_daemon.py, 16 Tests)

**TestBuildLlamaArgs (13)** — Aufrufsignatur `(template, config)`, config trägt nur
`default_binary`:

| Test | Template | Erwartete Argumente |
|---|---|---|
| minimal_template | nur model_path | `[binary, --model, <pfad>, --host, 127.0.0.1, --port, 8080]` an exakt diesen Positionen |
| key_value_params | ctx_size, n_gpu_layers, n_cpu_moe, parallel, spec_type=draft-mtp, spec_draft_n_max, cache_type_k=v, cache_type_v, chat_format, temp, top_p, top_k, min_p, seed | jedes `--flag <wert>`-Paar vorhanden (Werte als Strings, float 0.8/0.95/0.01) |
| flash_attn_as_kv | flash_attn="on" | `--flash-attn on` (select → key-value, kein Bool) |
| flash_attn_disabled | flash_attn="off" | `--flash-attn off` |
| boolean_flags | load_mode=mmap+mlock, jinja=true | `--load-mode mmap+mlock` und `--jinja` |
| boolean_flags_false | jinja=false | **kein** `--jinja` (und, damalige Prüfung, kein `--load-mode` — nicht gesetzt) |
| boolean_flags_missing | (nichts) | keine Bool-Flags |
| custom_binary | binary=custom | args[0] == custom |
| fallback_binary | ohne binary | args[0] == config.default_binary |
| custom_port | port=9000 | `--port 9000` |
| custom_bind | bind=0.0.0.0 | `--host 0.0.0.0` |
| additional_kv_params | logit_bias, penalties (alle vier korrekten Namen!), mirostat=2, mirostat_lr, split_mode, main_gpu, tensor_split, lora | Flags vorhanden; **`--mirostat-ent` fehlt** (gilt nur für Mirostat 1; hier 2) |
| (TestAddKv ×3) | key vorhanden/fehlt/Typen | append-verhalten + String-Konvertierung (bool → "True") |

**TestAddKv (3)**: key vorhanden → Paar angehängt; fehlt → nichts; int/float/bool → Strings
("42", "3.14", "True").

### 4.4 Parameter-Tabelle & Abhängigkeiten (test_params.py, 21 Tests)

**TestTable (5) — Struktur-Integrität**:
1. Schlüssel und Flags je einmalig.
2. Jeder select hat Optionen.
3. Jede bool-Flag beginnt mit `--`.
4. Jedes off_flag == `--no-` + Flag-Body.
5. GROUPS == erste-Auftreten-Reihenfolge der Gruppen in PARAM_SPECS (dict.fromkeys-Vergleich).

**TestDependencies (9) — when-Semantik**:

| Test | Assertion |
|---|---|
| draft_params_inactive_without_spec | alle `spec_draft_*` inaktiv bei spec_type="none" |
| draft_params_active_for_draft_type | spec_draft_n_max aktiv bei "draft-mtp" |
| draft_params_inactive_for_ngram_type | inaktiv bei "ngram-simple" |
| ngram_params_scoped_to_their_type | simple-Params aktiv nur bei "ngram-simple", nicht bei "ngram-mod"/"draft-mtp" |
| spec_type_accepts_comma_separated_list | "ngram-mod,draft-mtp" → draft aktiv; "ngram-mod,ngram-simple" → draft inaktiv |
| top_k_inactive_with_mirostat | top_k/top_p inaktiv bei mirostat 1/2; aktiv bei 0 |
| mirostat_lr_and_ent_scoped_by_version | lr aktiv bei 1 und 2; ent nur bei 1 |
| dry_params_need_multiplier | dry_base inaktiv bei 0.0, aktiv bei 0.8 |
| yarn_params_need_rope_scaling | inaktiv bei "linear", aktiv bei "yarn" |
| tensor_split_need_split_mode | inaktiv bei "none", aktiv bei "layer" |
| pooling_needs_embedding | aktiv bei embedding=true, inaktiv bei {} |

**TestBuildArgsWithDependencies (8) — Emission respektiert when**:
spec_draft_n_max ausgelassen bei spec_type=none (aber --spec-type selbst emittiert);
emittiert bei draft-mtp (Wert 4); top_k ausgelassen bei mirostat=1 (aber --mirostat da);
webui=false → `--no-webui`; metrics=true → `--metrics`; metrics=false ohne off_flag →
**weder** `--metrics` noch `--no-metrics`; leere Strings (lora="", api_key="") → nichts;
Defaults port=8080/host=127.0.0.1 immer emittiert.

**TestPruneDefaults (3)**: is_default erkennt Übereinstimmung (cache_type_k f16 = Default,
q8_0 nicht); Leerstring zählt als Default (lora ""); prune_defaults entfernt Defaults **und**
inaktive Keys, behält Nicht-Defaults — Template `{cache_type_k: f16, temperature: 0.8,
top_k: 20, spec_type: none, spec_draft_n_max: 4, lora: ""}` wird exakt zu `{top_k: 20}`.

### 4.5 Wizard-Introspektion & Finalisierung (test_wizard.py, 15 Tests)

**Fake-Hilfetext** (Fixture): enthält `--ctx-size N` (Platzhalter!), `--flash-attn
[on|off|auto]`, `--cache-type-k TYPE` mit mehrzeiligem `allowed values: f32, f16, q8_0`
+ `(default: f16)`, `--load-mode MODE` mit `- value: desc`-Bullets (auto, mmap,
mmap+mlock), `--webui, --no-webui`, `--metrics` (ohne Negation), `--spec-type
none,draft-mtp,ngram-simple` (bare Form).

**TestRuntimeSpecs (10)**:

| Test | Erwartung |
|---|---|
| unknown_flags_are_dropped | port fehlt (--port nicht in Hilfe), ctx_size bleibt |
| inline_values_parsed | flash_attn-Optionen == [on, off, auto] |
| allowed_values_parsed | cache_type_k-Optionen == [f32, f16, q8_0] |
| bullet_values_parsed | load_mode-Werte == ["", "auto", "mmap", "mmap+mlock"] (unset-Eintrag vorn) |
| bare_values_parsed | spec_type-Optionen == [none, draft-mtp, ngram-simple] |
| unset_entry_is_kept | load_mode.options[0][0] == "" |
| off_flag_dropped_when_build_lacks_it | metrics.off_flag == None; webui.off_flag == "--no-webui" |
| specs_without_help_value_list_keep_static_options | ctx_size-Optionen behalten statische Liste (0, 8192, 16384, …) — "N" ist kein Wert |
| unqueryable_binary_falls_back_to_full_table | leerer Hilfecache → exakt PARAM_SPECS |
| no_binary_falls_back_to_full_table | None → exakt PARAM_SPECS |

**TestFinalizeTemplate (7)** (ja/nein-Prompt ersetzt; Default-Pruning-Ja vorbesetzt):

| Test | Template | Ergebnis |
|---|---|---|
| inactive_keys_are_removed | {spec_type: none, spec_draft_n_max: 4} | {} (spec_type "none" ist auch Default → mitgepruned) |
| inactive_removed_but_non_default_kept | {spec_type: ngram-mod, spec_draft_n_max: 4} | {spec_type: ngram-mod} |
| defaults_are_dropped | {cache_type_k: f16, temperature: 0.8, top_k: 20} | {top_k: 20} |
| defaults_kept_when_declined | {cache_type_k: f16, top_k: 20}, Antwort nein | unverändert |
| always_passed_keys_are_never_pruned | {port, bind, name, model_path, binary} mit Default-werten | unverändert (ALWAYS_KEEP) |
| legacy_chat_format_is_migrated | {chat_format: chatml} | {chat_template: chatml} |
| explicit_chat_template_wins_over_legacy | {chat_format: chatml, chat_template: gemma} | {chat_template: gemma} (Legacy verworfen) |

### 4.6 Edit-Kommando (test_edit.py, 18 Tests)

**Fixtures**: `template_dir` — isoliertes Templates-Verzeichnis mit einem Template `test`
(port 8080, ctx_size 32768, cache_type_k q8_0, top_k 20, spec_type none, echte
model/binary-Pfade, `_expand_path` identisch gepatcht, `_find_llama_servers` → []).
`answers` — geordnete Antwortliste für input + _prompt_yes (Leer-String = Default).
`answers_by_prompt` — regex → Antwort-Map; **unmatchte ja/nein-Prompts werden mit nein
beantwortet** (Gruppen werden abgelehnt).

**TestResolveParameter (8)**:

| Test | Erwartung |
|---|---|
| accepted_spellings (parametrisiert: cache_type_k / --cache-type-k / cache-type-k / cachetypek) | immer spec.key == "cache_type_k", note None |
| negated_flag_resolves_to_its_parameter (webui / --webui / no-webui / --no-webui) | spec.key == "webui" |
| unknown_name_suggests_a_close_match | "top_ky" → spec None, Hinweis enthält "top_k" |
| substring_suggestion | "cache_type" → Hinweis enthält "cache_type_k" |
| no_suggestions_still_reports_unknown | "zzzzzzzz" → `Unknown parameter.` |

**TestEditSingleParameter (10)**:

| Test | Antworten | Ergebnis |
|---|---|---|
| change_existing_value | "1", "2" (Template 1, Auswahl f16) | cache_type_k == "f16" gespeichert |
| unchanged_value_can_be_removed | "1", "", "y" | top_k entfernt |
| unchanged_value_is_kept_when_removal_declined | "1", "", "n" | top_k bleibt 20 |
| missing_parameter_is_added | "1", "4096" | batch_size == 4096 hinzugefügt |
| custom_select_value_keeps_its_numeric_type | "1", "34" (custom-Eintrag), "262144" | ctx_size == **262144 als int** (nicht String) |
| default_valued_parameter_offers_to_store_it | "1", "", "y"; top_k vorher entfernt | top_k == 40 (Default) explizit gespeichert |
| default_valued_parameter_is_skipped_when_declined | "1", "", "n" | top_k nicht im Template |
| unknown_parameter_leaves_template_untouched | "1" | Template unverändert (BASE_TEMPLATE) |
| inactive_parameter_is_refused | "1" + Abbruchfolge | spec_draft_n_max bleibt draußen |
| inactive_parameter_asks_for_its_prerequisite | "1", "4", "4" | spec_type == "draft-mtp" **und** spec_draft_n_max == 4 gespeichert |

**TestEditWholeTemplate (3)**:

| Test | Regeln | Ergebnis |
|---|---|---|
| walk_updates_and_saves | Nur "Configure Sampling?" mit ja, `^top_k ` → "25" | top_k == 25 gespeichert |
| declining_every_group_changes_nothing | nur Template-Auswahl | top_k bleibt 20 |
| walk_removes_inapplicable_keys | spec_draft_n_max=4 injiziert, sonst ablehnen | spec_draft_n_max beim Speichern entfernt |

### 4.7 llm-stop / llm-start (test_llm_stop.py, 11 Tests)

**TestLlmStoppedConfig (6)**: llm_stopped default false; gespeichertes true wird geladen;
save_llm_stopped(true/false) persistiert und lädt zurück (auch Datei-Check);
save erhält andere Felder; save bei fehlender config.json → ConfigError
`Config file not found`.

**TestLlmStoppedState (1)**: DaemonState hat llm_stopped=false und beide Ereignisse
als threading.Event.

**TestCheckAndRecoverLlmStopped (4)** — Daemon mit config {restart_timeout_sec: 10},
globaler Zustand mit active_template "test_model", Port-Scan/Template-Laden/Health
gepatcht:

| Test | Setup | Erwartung |
|---|---|---|
| llm_stopped_pid_none_keeps_stopped | llm_stopped=true, pid=None | process_state == "stopped", pid bleibt None (nichts gestartet) |
| llm_stopped_pid_set_stops_running | llm_stopped=true, pid=4242, running; stop ersetzt (Zähler) | stop **genau einmal** aufgerufen; process_state == "stopped" |
| llm_stopped_pid_set_already_stopped_noop | llm_stopped=true, pid=None, stopped | stop 0-mal (nichts zu tun) |
| llm_not_stopped_still_checks_health | llm_stopped=false, pid=4242, health true | process_state == "running", health_ok true |
| llm_stopped_still_checks_health_when_false | Regression: llm_stopped false **early-returned nicht**, pid=7777 | health_ok true (normaler Zyklus läuft) |

### 4.8 Stoppfad-Budgets & Reaping (test_stop_shutdown.py, 7 Tests)

Kindprozess-Code CHILD_FAST: SIGTERM → sofort os._exit(0), gibt "up" aus, dann Schlafschleife.
Fixture `server_child`: echter Prozess, readline auf "up", Teardown kill+wait.

| Test | Erwartung |
|---|---|
| watchdog_default_covers_graceful_stop | shutdown_watchdog_seconds() == 23.0 (10 + 13) |
| watchdog_is_config_driven | restart_timeout_sec=3 → 16.0 (3 + 13) |
| watchdog_falls_back_when_config_unreadable | fehlende Datei → 23.0 (Fallback 10 + 13) |
| cli_restart_timeout_default | _restart_timeout_sec() == 10 |
| cli_restart_timeout_reads_config | restart_timeout_sec=5 → 5 |
| cli_restart_timeout_survives_bad_config | fehlende Datei → 10 |
| daemon_stop_reaps_child | daemon.stop() (server_owned, echte Kind-PID): state.pid None, Kind beendet (poll ≠ None), get_process_state.alive **false** (gereapet — kein Zombie!) |

### 4.9 Prozessmonitor (test_process_monitor.py, 4 Tests — echte Kindprozesse)

CHILD_FAST wie oben; CHILD_SLOW: SIGTERM-Handler schläft 30 s (simuliert harten Prozess).

| Test | Erwartung |
|---|---|
| zombie_counts_as_terminated | nach SIGTERM + 0.5 s (unreaped): state.zombie true; kill_process_graceful(pid, 10) → true in **< 2.5 s** (alter Bug: volle 10 s + 5 s Budget) |
| alive_child_is_terminated_quickly | kill_process_graceful → true in < 2.5 s; proc.poll() ≠ None |
| slow_child_escalates_to_sigkill_within_budget | timeout=1 → true; Dauer ≥ 0.9 und < 8.0 s; poll() == -SIGKILL |
| unreaped_child_reported_alive_then_gone_after_reap | unreaped: alive **true** (das war der Endlos-Restart-Bug); nach wait()/reap: alive false |

### 4.10 models-Kommando (test_models.py, 3 Tests)

Fixture `fake_models_dir`: HOME auf tmp setzen; GGUF-Dateien = 16 Null-Bytes mit .gguf-Endung.

| Test | Erwartung |
|---|---|
| models_lists_missing | Model mit Template versteckt, Model ohne Template gelistet |
| models_all_covered | alle covered → `All models already have templates.` |
| models_with_templates_basenames | _models_with_templates liefert Basisnamen ladbarer Templates; kaputtes JSON wird stillschweigend übersprungen (kein Raise) |

### 4.11 Datengetriebene Totalabdeckung (test_all_specs.py, 4 × 206 parametrisierte Tests)

**Parametrisierung**: `@pytest.mark.parametrize("spec", params.PARAM_SPECS, ids=<schlüssel>)`
— **jede** der 206 Spez läuft durch alle 4 Tests → 824 Testfälle (davon viele geskippt,
siehe Skip-Regeln). Gesamtzahl der Suite: 646 passed + 321 skipped.

**Wert-Generator `_sample(spec)`**: bool → true; select → erster Optionswert, der **nicht**
in ("", "none", "off", 0, False) liegt (damit Gates wie spec_type wirklich aktivieren),
Fallback erster nicht-leerer Optionswert, sonst Fail; int → 7; float → 0.75; str →
"test-value".

**Kontextgenerator `_satisfying_template(spec)`**: Template = `{model_path: /m.gguf,
<schlüssel>: <musterwert>}`; bis zu **3 Runden** (Gates können selbst Gates haben —
denselben Tiefe-Grad wie der CLI-Walk): solange inaktiv, `enabling_values` hinzumergen.
Bleibt inaktiv → null.

| Test | Skip-Regel | Assertion |
|---|---|---|
| test_when_predicate_tolerates_empty_template | nie | `spec.active({})` liefert bool (kein Raise bei fehlenden Gate-Schlüsseln) |
| test_flag_emitted_with_satisfied_context | — (fail statt skip, wenn kein aktivierender Kontext ableitbar: `no automatically derivable context activates its when predicate`) | bool: Flag in args; sonst Flag **und** Wert (`args[flag+1] == str(wert)`) |
| test_false_bool_negates | nicht bool → skip; kein aktivierbarer Kontext → skip | false + off_flag vorhanden → off_flag in args; ohne off_flag → weder flag noch off_flag |
| test_inactive_spec_pruned_from_args | ohne when → skip; bereits aktiv ohne Gates → skip (anderswo geprüft) | Flag fehlt in args (wenn Kontext die Aktivität verweigert) |

**Bedeutung**: ein neuer oder vertippter Tabelleneintrag fällt **hier**, nicht in Produktion.

### 4.12 Abdeckungs- & Abhängigkeitsregeln (verbindlich)

1. **Alle 10 Testdateien** müssen als Suite bestehen: `pytest -v` (Repo-Konvention: Tests
   sind das **einzige** Verifikations-Gate; kein Lint/Typecheck konfiguriert).
2. **Sequenzunabhängigkeit**: keine Reihenfolge-Abhängigkeiten zwischen Tests; jeder Test
   baut seine Isolation selbst auf (Fixtures + monkeypatch). Ausnahme: die Prozess-Tests
   (4.8/4.9) brauchen echte Kindprozesse (POSIX) — auf Windows nicht lauffähig.
3. **Zwingend bestehende Referenz-Tests** (Verhaltensverträge, die Regressions-Bugs
   dokumentieren): zombie_counts_as_terminated (< 2.5 s), unreaped_child_reported_alive
   (Zombie zählt als lebendig bis reap), daemon_stop_reaps_child, watchdog/CLI-Budget-Tests,
   llm_stopped_still_checks_health_when_false, models-Basenames-Skip-Verhalten.
4. **Gesamtergebnis der Referenz-Suite**: 646 passed, 321 skipped (Skips sind legitime
   Fall-Ausschlüsse per Skip-Regel, **keine** Fehler).

## 5. Formatkonventionen

### 5.1 Namenskonventionen

| Objekt | Konvention | Beispiele |
|---|---|---|
| Modul | snake_case, kurze Domänenbegriffe | konfiguration, daemon, api, cli, params, health, process_monitor, main |
| Funktion | snake_case, Verben für Aktionen | load_config, build_args, check_and_recover |
| Interne Hilfen | führender Unterstrich | _resolve_parameter, _close_keys, _prompt_yes |
| Konstante | UPPER_SNAKE auf Modulebene | PARAM_SPECS, CORE_KEYS, GROUPS, ALWAYS_KEEP, HELP_CACHE |
| Datenklasse | PascalCase, Felder snake_case | DaemonState, ProcessState, ParamSpec |
| Template-Schlüssel | snake_case == Flag-Body (Ausnahmen in Tabelle dokumentiert, z. B. temperature → `--temp`) | cache_type_k → `--cache-type-k` |
| Config-Schlüssel | snake_case | check_interval_sec, restart_timeout_sec |
| Booleans im Template | echte Bools, nie Strings | `"jinja": true` |
| Off-Form | `--no-` + Flag-Body | `--no-webui` |

### 5.2 Dateistruktur

```
<paketwurzel>/
├── <paketverzeichnis>/        # Implementierung, flach (keine Subpakete)
│   ├── main.py                # Einstiegspunkt (Dispatch + Exit-Codes)
│   ├── cli.py                 # alle Befehle + Wizard (größtes Modul)
│   ├── daemon.py              # Zustand, PID, Arg-Bau, Lebenszyklus, Schleife
│   ├── api.py                 # HTTP-API (Standardbibliothek-Server)
│   ├── config.py              # Config/Template laden/validieren/speichern, Home-Regel
│   ├── params.py              # Parameter-Tabelle + Abhängigkeitsregeln
│   ├── health.py              # Health-Check (asynchron + Sync-Wrapper)
│   └── process_monitor.py     # Prozessstatus, Zombie, graceful kill
├── tests/                     # pytest, konftest mit Isolations-Fixtures
├── scripts/<unit-name>.service  # systemd-Unit (spiegelbildlich zur Generierung)
├── README.md, AGENTS.md
└── pyproject.toml             # Metadaten + Konsolenskript
```

**Verbindlich**: die Parameter-Tabelle liegt in **einem** Modul und wird von Daemon und
Wizard importiert (niemals dupliziert); die Home-Auflösung existiert **einmal** und wird
delegiert.

### 5.3 Konfigurations-Serialisierung

- JSON, UTF-8, Einrückung **4 Leerzeichen** (config.json, Templates, API-Antworten: die
  API nutzt kompakte Serialisierung ohne Einrückung, `--json`-Ausgabe des status-Befehls
  Einrückung 2, `list-templates` Einrückung 2).
- Templates: alle relevanten Felder flach auf oberster Ebene (keine Verschachtelung).
- Zeilenumbrüche im Dateisystem: Standard des Laufzeitsystems; JSON selbst ohne
  Zeilenumbruch am Ende optional (nicht verifiziert — tests vergleichen per json.load).

### 5.4 Ausgabeformatierung

- Prompt-Formate exakt wie in 3.8.11; Nummerierungen 1-basiert; Einrückung mit
  Leerzeichen (2/4/8 je Kontext wie in den Beispielen).
- Erfolgsmarkierungen: `✓` (Unicode) für erledigte Aktionen; `⚠` für Warnungen;
  `◀` als "aktuell"-Marker in Listen.
- Log-Präfix des Daemon für Server-Ausgaben: `[llama-server] `.

### 5.5 Dokumentation & Stil

- Modul-Docstring: Zweck + Wer-Nutzt-Was-Vertrag (besonders die Parameter-Tabelle).
- Keine Docstring-Pflicht für jeden Test (aber beschreibende Namen, die den Vertrag nennen).
- Fehlermeldungen: Satzanfang, ohne Punkt am Ende (Ausnahmen in 2.3/2.4 verbindlich).
- Komplexität: Wizard in kleine, benannte Hilfsfunktionen (Prompt/Resolve/Finalisieren)
  zerlegt; der Daemon-Entscheidungsbaum in einer Funktion mit frühem return.

---

## 6. Verifizierungskriterien (100 %-Äquivalenz)

### 6.1 Zwingende Test-Suite

Die komplette Suite aus Abschnitt 4 (alle 10 Dateien) muss **ohne Modifikation** der
Assertionen bestehen; erwartetes Ergebnis **646 passed / 321 skipped / 0 failed**.
Tests, die echte Prozesse starten (4.8, 4.9), sind POSIX-gebunden.

### 6.2 Manuelles Verifizierungsprotokoll (identisches Verhalten)

| # | Aktion | Erwartetes identisches Ergebnis |
|---|---|---|
| 1 | `llama-monitor` (ohne Argument) | Hilfe-Text, Exit 1 |
| 2 | `llama-monitor list-templates` | JSON-Array (sortiert), Einrückung 2, Exit 0 |
| 3 | `llama-monitor status` / `--json` | Format exakt 3.8.4 |
| 4 | `llama-monitor models` | Format exakt 3.8.16 |
| 5 | `llama-monitor start` (nicht-root, GUI) | pkexec-Dialog; nach Elevation Daemon-Ausgaben exakt 3.5.8 |
| 6 | `llama-monitor stop` | PID-Pfad oder API-Pfad exakt 3.8.2 |
| 7 | API `GET /api/v1/status` | Body exakt 3.7.1 |
| 8 | API `POST /api/v1/set-template` mit bekanntem/unkanntem Namen | 200/400-Bodies exakt |
| 9 | `llama-monitor llm-stop` → Status → `llm-start` | Flag persistiert; Daemon stoppt Server, startet nicht wieder, dann Resume |
| 10 | `llama-monitor reload-template <name>` (Daemon läuft/nicht) | Ausgaben + Exit-Codes exakt 3.8.6 |
| 11 | Template-Wechsel bei fremdgestartetem Server auf Template-Port | Adoption (Meldungen exakt 3.5.7/3.5.8) |
| 12 | Server killen (SIGKILL) → Daemon-Verhalten | Erkennung + Neustart mit exakten Meldungen |
| 13 | `enable-autostart --sudo` → Unit-Inhalt vergleichen | byte-identisch zu 3.8.8 |
| 14 | Wizard `create-base` → Template-Datei | Feldauswahl, Pruning, Migrationsregeln exakt 3.8.12–3.8.13 |
| 15 | `edit <parameter>` mit allen vier Namens-Schreibweisen | Auflösung + Prompts exakt 3.8.17 |
| 16 | `edit <gated-parameter>` mit inaktivem Kontext | Voraussetzungs-Dialog exakt (Tiefe 3, Abbruch) |
| 17 | Zeitbudgets messen (stop, shutdown, Zombie-kill) | Werte aus 3.14 eingehalten |

### 6.3 Äquivalenzdefinition

Zwei Implementierungen gelten als **100 % äquivalent**, wenn bei identischem Input:

1. **Exit-Codes** identisch sind (alle Befehle, alle Fehlerpfade).
2. **stdout/stderr-Texte** identisch sind (bis auf die dokumentierten Laufzeit-Variablen:
   PIDs, Pfade, Timestamps, Dateigrößen, Verfügbarkeitslisten der Umgebung).
3. **JSON-Bodies** der API strukturell und inhaltlich identisch sind (Schlüsselreihenfolge
   egal, Werte identisch inkl. null-Handling).
4. **Dateieffekte** identisch sind: config.json-Feldbestand, Template-Inhalte, PID-Datei,
   Unit-Inhalt byte-identisch (Platzhalter ausgeschrieben).
5. **Prozesseffekte** identisch sind: Kindprozess-Argumentliste (Reihenfolge!), Signalfolge
   (SIGTERM → Budget → SIGKILL), Reaping-Verhalten, Threads-Menge.
6. **Timing-Budgets** eingehalten sind (die Schwellen aus 3.14, ±Scheduling-Toleranz).
7. **Zustandsmaschinen-Übergänge** identisch sind (stopped→starting→running/crashed/…).

Nicht Teil der Äquivalenz: Laufzeit-Sprachdetails, Timing unter Last, Reihenfolge
paralleler Threads außerhalb der spezifizierten Verträge.

---

## 7. Implementierungsanweisungen

### 7.1 Reihenfolge (von null zur fertigen Implementierung)

1. **Konfigurationsschicht** (config.py-Äquivalent): Pfade + Home-Regel → load/validate
   config → load/validate/save templates → save_active_template/save_llm_stopped →
   Expand-Helfer. Sofort mit 4.2 testen.
2. **Parameter-Tabelle** (params.py): ParamSpec-Datentyp → alle 206 Einträge in exakter
   Reihenfolge → abgeleitete Strukturen → Hilfsfunktionen. Sofort mit 4.4 testen.
3. **Argument-Emission** (daemon._build_llama_args) → 4.3 + 4.11 testen.
4. **Prozessmonitor** → 4.9 testen (echte Prozesse nötig).
5. **Health-Check** → Verhaltensvertrag 3.6 (Tests implizit über Daemon-Suite).
6. **Daemon-Zustand + Lebenszyklus + Schleife** → 4.7, 4.8 (Budgets!) testen.
7. **HTTP-API** → 3.7-Verträge testen (Status/Template/llm/shutdown inkl. Watchdog).
8. **CLI-Basis** (Dispatch, Elevation, start/stop/restart/status/list/set/logs/llm-*) →
   Ausgabeverträge 3.8 manuell verifizieren.
9. **Wizard** (Prompts, Introspektion, Finalisierung, create/„edit"/delete/models) →
   4.5, 4.6 testen.
10. **systemd-Integration** (enable/disable) → Unit byte-identisch.
11. **Suite komplett laufen lassen** → 646/321/0.

### 7.2 Alternative Implementierungswege

| Fehlende Technologie | Ersatz | Beachte |
|---|---|---|
| httpx | jeder HTTP-Client mit Timeouts (sync + async, oder 2 Klassen) | Antworttext-Trimmen + JSON-Normalisierung nachbauen |
| asyncio | Threads + blockierender HTTP-Client | Sync-Wrapper-Fallback 3.6 ist dann der Hauptweg; Loop-Unterbrechung im Signal-Handler entfällt (dokumentieren!) |
| http.server-Äquivalent | jeder threaded HTTP-Server | Request-Logging unterdrücken; 404-Vertrag beibehalten |
| systemd | jeder Supervisor mit env-Injektion + Neustart | Unit-Felder 1:1 abbilden (Restart, RestartSec, Environment) |
| pkexec | nur sudo-Pfad | GUI-Dialog-Zweig entfällt; Meldung bei fehlendem sudo bleibt |
| ss | /proc/net/tcp-Parsing oder socket-Tests | `:port `-Trennzeichner-Regel nachbilden, pid=Extraktion entsprechend |
| ps | /proc/<pid>/stat | Status-Zeichen-Map identisch (R/S/Z/T/sonst) |
| journalctl | Datei-Logs | `logs` liest die Datei; Format der Kopfzeile beibehalten |

### 7.3 Best Practices (aus dem Original abgeleitet)

1. **Eine Quelle pro Fach**: Parameter-Tabelle, Home-Auflösung, Timeout-Ableitung —
   jedes existiert genau einmal und wird delegiert.
2. **Budgets ableiten, nicht konstant setzen**: jede Wartezeit, die ein graceful
   Teardown behindern könnte, speist sich aus `restart_timeout_sec`.
3. **Zombie-Bewusstsein**: „beendet" = nicht-alive **oder** zombie; reapen nur der
   Handle-Besitzer.
4. **Nicht-blockierend im Signal-Kontext**: Crash-Sonde mit kurzem Poll statt blockierendem
   wait; Signal-Handler unterbricht blockierende Event-Loops.
5. **Fehlertolerante Validierung mit exakten Meldungen**: Validierungsreihenfolge ist Teil
   des Vertrags.
6. **Wizard bleibt benutzbar**: Introspektionsfehler → statische Tabelle, nie Absturz.
7. **Persistente Flags sofort schreiben** (llm_stopped), Warnung bei Fehlschlag, Betrieb
   weiter.

### 7.4 Vermeidungsliste (auf keinen Fall)

1. **Keine** festen Zeitbudgets, die kleiner als das graceful-Teardown-Budget sind
   (verwaiste llama-server).
2. **Kein** Doppel-Fork/Daemonizing des Daemons (systemd Type=simple).
3. **Kein** `os.waitpid` in geteilten Helfern (zerstört Popen.returncode; nur Daemon.stop
   reapet über das Handle).
4. **Keine** zweiten Implementierungen von Home-Auflösung oder Parameter-Tabelle.
5. **Keine** Bool-Emission `--flag false` (immer off_flag oder Abwesenheit).
6. **Keine** Defaults in die Argumente füllen (fehlender Key = kein Flag).
7. **Keine** Emittierung inaktiver (when-falscher) Parameter.
8. **Kein** SIGKILL des Daemons vor Ablauf der vollen Budgets (API-Watchdog
   restart_timeout+13, CLI restart_timeout+18).
9. **Kein** Wiederherstellen des Servers solange llm_stopped=true.
10. **Keine** Ports-Defaults beim Wizard-Pruning anfassen (ALWAYS_KEEP).
11. **Kein** Root-Signal an PID-Dateien fremder Nutzer ohne Fallback-Prüfung
    (/root-Fallback!).
12. **Keine** Port-Parser ohne Trennzeichenerkennung (`:8080` vs `:80801`).
13. **Keine** Prompt-Silence-Änderungen: `Y/n` vs `y/N` Groß-/Kleinschreibung ist
    beobachtbar.
14. **Kein** Aufruf von `--mirostat-ent` bei mirostat≠1 (Version-Gate!).

---

## Anhang A: systemd-Unit (exakt, Platzhalter ersetzt für Nutzer `n4rf`)

```ini
[Unit]
Description=Llama Server Monitor
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/llama-monitor start
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=HOME=/home/n4rf
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=/home/n4rf/.config/llama-monitor

[Install]
WantedBy=multi-user.target
```

(`Environment=HOME=` trägt das Ergebnis der Home-Regel 2.3; `WorkingDirectory` =
`<Home>/.config/llama-monitor`. Beide werden bei der Generierung aus derselben Quelle
abgeleitet.)

## Anhang B: Fehlermeldungs-Katalog (sortiert, exakt)

**Konfiguration**: `Config file not found: <PFAD>` · `Missing required config field:
<SCHLÜSSEL>` · `'enabled' must be a boolean` · `'check_interval_sec' must be a positive
number` · `'default_binary' must be a non-empty string` · `'active_template' must be a
non-empty string` · `Active template '<NAME>' not found in templates directory` ·
`Default binary not found: <PFAD>` · `Templates directory not found: <PFAD>` ·
`Template '<NAME>' not found in templates directory`

**Template**: `Template missing required field: 'model_path'` · `Model file not found:
<PFAD>` · `Template binary not found: <PFAD>` · `Invalid port in template: <port>` ·
`Invalid bind address in template: <bind>` · `Template not found: <NAME>`

**Wizard/CLI**: `Base template not found: <PFAD>` · `No GGUF model files found in
~/models/` · `No ~/models/ directory found.` · `Invalid selection. Use 1-<n>.` ·
`No templates available.` · `No templates available to delete.` · `No templates to edit.`
· `No existing templates to copy from.` · `Error: Template '<N>' not found.` ·
`Available templates: <liste>` · `Aborted.` · `Nothing changed.` · `Unknown parameter.`
· `Parameter '<k>' (<flag>) is not supported by the selected llama-server build.` ·
`Did you mean: <vorschläge>?` · `<key> still does not apply with these values.` ·
`  <key> is currently '<anzeige>'` · `  -> <key> = <repr>` · `  -> removed <key>` ·
`Removed <n> inapplicable parameter(s): <keys>` · `Migrated chat_format -> chat_template
(<wert>)` · `✓ Template saved: <pfad>` · `✓ Template deleted: <pfad>` ·
`✓ Model deleted: <pfad>` · `Model file kept.` · `Model file kept — still used by
template '<name>': <pfad>`

**Daemon/API/CLI (Laufzeit)**: siehe Abschnitte 3.5, 3.7, 3.8 und 3.11.

## Anhang C: Begründungsregister (Entscheidung → Grund)

| Entscheidung | Grund |
|---|---|
| Eine Parameter-Tabelle | Daemon und Wizard dürfen nie auseinanderdriften; historisch taten sie es (falsche Flag-Namen) |
| when-Prädikate statt Sonderfälle | stale Template-Keys verschwinden automatisch statt widersprüchliche Flags zu erzeugen |
| Vordergrund-Daemon | systemd übernimmt Lebenszyklus |
| pkexec vor sudo | keine Terminal-Passwort-Prompts aus GUI-/Chat-Kontexten |
| stop ohne Root | die API gehört dem laufenden Daemon und bedient jeden lokalen Nutzer |
| Budgets aus restart_timeout_sec | der Signal-Handler stoppt zuerst den Server; frühere Kills verwaisten ihn (beobachteter Bug) |
| Zombie = beendet | Endlos-Restart-Schleifen auf unreapeten Kindern (beobachteter Bug) |
| Reapen nur in Daemon.stop | Popen.returncode-Semantik; Doppel-Reap zerstört Status |
| --help-Introspektion | Builds unterscheiden sich (Turbo-Quants, fehlende --no-Formen) |
| llm_stopped persistiert | Daemon-Neustart darf den gestoppten Zustand nicht vergessen |
| Loop-Unterbrechung im Signal-Handler | blockierender Health-Wait hätte SIGTERM bis 3 s verzögert |
| shutdown → 200 sofort + SIGTERM später (0.5 s) | Antwort muss raus, bevor der Prozess stirbt |
| Watchdog restart_timeout+13 | volle graceful-Teardown-Zeit plus Reserve; früheres SIGKILL = verwaister Server |

---

**Ende des Kontextdokuments.** Jede Angabe in diesem Dokument ist verbindlich; wo das
Original Verhalten zeigt, das hier nicht beschrieben wäre, wäre das Dokument unvollständig —
die Test-Suite (Abschnitt 4) ist die objektive Instanz, die das überprüft.

