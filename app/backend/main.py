import os
import uvicorn
from backend.config import BASE_URL, LOG_FILE, PORT, log_line, set_status
from backend.database import _db_connect, _ensure_main_tables, list_devices
from backend.backup import write_pair_record_file, import_system_config_from_fs


def main():
    set_status(False)
    # Clear or create log file
    with open(LOG_FILE, 'w'):
        pass

    # Ensure DB tables
    with _db_connect():
        pass
    _ensure_main_tables()

    # Sync pair-record files
    for user in list_devices().keys():
        try:
            write_pair_record_file(user)
        except Exception as e:
            log_line(f"Pair record sync skipped for {user}: {e}")

    # Sync SystemConfiguration.plist
    from backend.database import _get_system_config
    data = _get_system_config()
    if data is not None:
        try:
            from backend.config import PAIR_RECORD_DIR
            with open(os.path.join(PAIR_RECORD_DIR, "SystemConfiguration.plist"), "wb") as f:
                f.write(data)
        except Exception as e:
            log_line(f"SystemConfiguration write failed: {e}")
    else:
        import_system_config_from_fs()

    print(f"🚀 iPhone Backup Manager läuft auf http://0.0.0.0:{PORT}")
    if BASE_URL:
        print(f"📍 Base URL: {BASE_URL}")
    
    uvicorn.run("backend.api:app", host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()