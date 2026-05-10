import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_default_user_settings_use_google_tts():
    import config
    importlib.reload(config)
    assert config.DEFAULT_USER_SETTINGS["engine"] == "google"
    assert config.DEFAULT_USER_SETTINGS["voice"] in config.GOOGLE_VOICES
    assert config.DEFAULT_USER_SETTINGS["voice"] == "ko-KR-Standard-A"
