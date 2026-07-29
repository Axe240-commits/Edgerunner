@echo off
REM Einmal-Check (29.07.): Zeigt die gesammelten HL-Funding-Prints.
"C:\Users\Home PC\AppData\Local\Programs\Python\Python312\python.exe" -c "import sqlite3; c=sqlite3.connect(r'C:\edgerunner\hl_funding.db'); rows=list(c.execute('SELECT ts, coin, funding FROM prints ORDER BY ts')); print(len(rows), 'prints'); [print(r) for r in rows]"
