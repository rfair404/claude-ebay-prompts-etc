@echo off
REM Wrapper for the once-a-minute marble-index scheduled task (Task Scheduler:
REM "MarbleIndexNext"). Crawls ONE forum page per run and advances the cursor in
REM state.json. A lockfile in the index dir makes overlapping ticks skip, so
REM running every minute is safe even if a page takes >60s. Log is overwritten
REM each run (last-run only) to avoid unbounded growth; cumulative progress lives
REM in state.json (see `marble_index.py status`).
cd /d "C:\Users\Reuseum\Documents\Claude\Projects\ebaybiz"
"C:\Users\Reuseum\AppData\Local\Programs\Python\Python312\python.exe" lib\marble_index.py next > "kb\index\marbleconnection\next.log" 2>&1
