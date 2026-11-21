# Production run: no file watching and no rerun-on-save. (Graph steps also run in a background
# worker, so reruns can't cut them off; this just avoids pointless reruns and saves resources.)
# Usage, from the project folder:   .\scripts\run_prod.ps1
# On a host that uses environment variables instead of flags, set:
#   STREAMLIT_SERVER_RUN_ON_SAVE=false   STREAMLIT_SERVER_FILE_WATCHER_TYPE=none
$env:INTERVIEW_ARENA_DEBUG = "0"
.venv\Scripts\python.exe -m streamlit run app.py `
    --server.runOnSave false `
    --server.fileWatcherType none `
    --server.headless true
