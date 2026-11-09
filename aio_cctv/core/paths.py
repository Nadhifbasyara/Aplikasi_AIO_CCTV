from pathlib import Path

from platformdirs import user_data_dir

APP_DIR = Path(user_data_dir("aio-cctv", "tugasakhir"))
PROFILES_DIR = APP_DIR / "profiles"
CAMERAS_FILE = APP_DIR / "cameras.json"
DB_FILE = APP_DIR / "analytics.db"          # Fase 7
USER_MODULES_DIR = APP_DIR / "modules"      # modul yang dipasang lewat GUI (Fase 6)
for d in (APP_DIR, PROFILES_DIR, USER_MODULES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Bobot model masih ikut repo (paket mandiri menyusul di Fase 9)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = PROJECT_ROOT / "models"
BUNDLED_MODULES_DIR = PROJECT_ROOT / "modules"   # modul bawaan repo (baseline, pengembangan)
