# Geschützte Sicherungen

`ops/backup.py` sichert den vorhandenen Installationsstand ohne Dienstneustart,
Geräteschaltungen oder Datenbank-Checkpoint. Es benötigt nur Python 3.11+ und
läuft auf Linux/macOS. Nicht im laufenden App-Prozess importieren/starten.

```sh
python3 ops/backup.py create \
  --root /path/to/homematic-bridge \
  --destination /path/to/protected-backups \
  --keep 14 \
  --container homematic-bridge
```

Erfolg: `BACKUP_OK <manifest>`. Das Manifest verweist auf das zugehörige
`bridge-backup-<UTC-Zeit>-<Zufallskennung>.tar.gz`. Prüfung ohne Extraktion:

```sh
python3 ops/backup.py verify /path/to/bridge-backup-....tar.gz.json
```

Enthalten: Code, Konfiguration, Zertifikat und persistente Daten. Ausgeschlossen:
Git-Metadaten, virtuelle Umgebung, Logs, Python-/Test-Caches und Agent-Dateien.
SQLite wird über die Read-only-Backup-API konsistent gesichert; WAL-/SHM-Dateien
werden nicht getrennt kopiert. Verifikation prüft das komprimierte Archiv sowie
Pfad, Größe und SHA-256 jeder Datei. Ungültige Symlinks und Archiveinträge werden
abgelehnt. Gleichzeitige Backups werden durch eine Dateisperre verhindert.

Das Verzeichnis gehört dem ausführenden Benutzer und erhält Modus 0700, Archiv
und Manifest 0600. CLI-Logs enthalten keine Konfigurationsinhalte, Passwörter oder
rohen Docker-Umgebungsvariablen. Laufzeitmetadaten enthalten nur Image-ID und
Build-Commit. Das Docker-Image selbst ist **nicht** enthalten und muss für einen
exakten Software-Rückfall separat aufbewahrt werden.

`--keep` behält die neuesten 1–365 abgeschlossenen Sicherungen dieses Werkzeugs.
Bereinigung erfolgt erst nach erfolgreicher neuer Verifikation. Alte manuelle
Deployment-/Rückfall-Verzeichnisse werden nicht gelöscht. Fehlgeschlagene
Aufzeichnung veröffentlicht keine Teilsicherung. Scheitert nur die Bereinigung,
bleibt die neue verifizierte Sicherung trotzdem erhalten, aber der Lauf meldet
einen Fehler. Zusätzlich sind mindestens 128 MiB Sicherheitsreserve erforderlich.

## Grenzen und Wiederherstellung

Die Sicherungen enthalten Geheimnisse und sind **nicht verschlüsselt**. Sie dürfen
nicht in Git, ungeschützte Freigaben oder öffentliche Downloads. Verzeichnisrechte
schützen gegen andere lokale Benutzer, nicht gegen Root oder Datenträgerzugriff.
Keine stille Änderung von Schlüsseln oder Uploads an externe Dienste.

Ein Backup im selben Proxmox-Container schützt vor Update-/Bedienfehlern, nicht vor
Verlust des Containers, Proxmox-Hosts oder Datenträgers. Zusätzlich sollte ein
unabhängiges, verschlüsseltes Backup-Ziel bzw. Proxmox-Backup verwendet werden.
Ort/Zugangsdaten dafür müssen vorab ausdrücklich festgelegt werden.

Dateien werden während des Betriebs einzeln stabil kopiert; SQLite ist intern
konsistent, aber der ganze Installationsstand ist kein atomarer Dateisystem-
Snapshot. Verändert sich eine Datei wiederholt während des Kopierens, bricht der
Lauf ab. Für eine exakt zeitgleiche Gesamtsicherung Proxmox-/Filesystem-Snapshots
mit passender Datenbankstrategie verwenden.

Vor einer Wiederherstellung das Manifest prüfen und zuerst in ein privates
Testverzeichnis entpacken. Keine automatische Live-Wiederherstellung angeboten:
alte Zugangsdaten, Konfiguration und Messhistorie dürfen nicht blind aktuelle
Daten überschreiben. Erst nach Prüfung gezielt übernehmen; Image/Compose müssen
zum wiederhergestellten Code passen. Wiederherstellung unterbricht ggf. die Bridge.

Regelmäßige Ausführung und Aufbewahrung werden für die Installation ausdrücklich
festgelegt. Eine Server-Ausführung läuft unabhängig vom Mac/Codex; eine Codex-
Automation benötigt einen erreichbaren Mac, Netzwerk und SSH-Zugang. Das Werkzeug
ist in beiden Fällen gleich. Automatische Geräte-/Sirenenbefehle sind kein Teil
einer Sicherung oder ihrer Verifikation.
