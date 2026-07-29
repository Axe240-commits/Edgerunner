@echo off
REM Wrapper fuer den HL-Funding-Logger (Task "hl-funding-logger", stuendlich).
REM Zweck: schtasks-/tr kann keine verschachtelten Quotes — darum traegt
REM diese Datei den Python-Pfad (mit Leerzeichen) und die Log-Umleitung.
REM --once: holen, speichern, beenden (kein Dauerprozess, kein Haenger).
REM Ersteller: Hermes, 29.07.2026.
"C:\Users\Home PC\AppData\Local\Programs\Python\Python312\python.exe" C:\edgerunner\hl_funding_logger.py --once >> C:\edgerunner\hl_funding.log 2>&1
