import uvicorn
from backend.config import BASE_URL, LOG_FILE, PORT
from backend.database import _ensure_main_tables


def main():
    with open(LOG_FILE, "w"):
        pass
    _ensure_main_tables()

    print(f"🚀 iPhone Backup Manager läuft auf http://0.0.0.0:{PORT}{BASE_URL}")
    uvicorn.run("backend.api:app", host="0.0.0.0", port=PORT, reload=True)


if __name__ == "__main__":
    main()