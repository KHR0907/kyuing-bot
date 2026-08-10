from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_starts_supervisor_and_supervisor_starts_all_workers():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    supervisor = (ROOT / "supervisor.py").read_text(encoding="utf-8")
    assert 'CMD ["python", "supervisor.py"]' in dockerfile
    assert "await manager.start_enabled_bots()" in supervisor
    assert "protected_bot_ids" not in supervisor
