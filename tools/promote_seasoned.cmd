@echo off
REM Wrapper for the daily seasoned-promotion task (Task Scheduler:
REM "PromoteSeasoned", 6:00 AM). Adds every live listing >= $25 that has been up
REM >= 15 days to the "Revenue Seasoned 15d" cost-per-sale campaign. Never
REM removes an ad. Log is overwritten each run (last-run only); the plan of the
REM last run is in reports\promote_seasoned_last.json.
cd /d "C:\Users\Reuseum\Documents\Claude\Projects\ebaybiz"
"C:\Users\Reuseum\AppData\Local\Programs\Python\Python312\python.exe" tools\promote_seasoned.py --store default --confirm --json reports\promote_seasoned_last.json > reports\promote_seasoned.log 2>&1
