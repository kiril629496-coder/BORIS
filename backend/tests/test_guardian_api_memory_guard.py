from unittest.mock import patch
import boris_guardian


def _fake(values):
    def run(cmd, timeout=25):
        for key, value in values.items():
            if key in cmd:
                return (0, str(value))
        return (1, "")
    return run


def test_api_memory_guard_green_for_2g_and_bounded_arenas():
    vals={
        "MemoryHigh": 2*1024*1024*1024,
        "MemoryCurrent": 512*1024*1024,
        "Environment": "PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2",
    }
    with patch.object(boris_guardian, "sh", side_effect=_fake(vals)):
        out=boris_guardian.backend_api_memory_guard()
    assert out["bad"] == []


def test_api_memory_guard_rejects_old_1536m_contract():
    vals={
        "MemoryHigh": 1536*1024*1024,
        "MemoryCurrent": 512*1024*1024,
        "Environment": "MALLOC_ARENA_MAX=2",
    }
    with patch.object(boris_guardian, "sh", side_effect=_fake(vals)):
        out=boris_guardian.backend_api_memory_guard()
    assert len(out["bad"]) == 2
    assert all("MemoryHigh" in x for x in out["bad"])


def test_api_memory_guard_requires_malloc_arena_bound():
    vals={
        "MemoryHigh": 2*1024*1024*1024,
        "MemoryCurrent": 512*1024*1024,
        "Environment": "PYTHONUNBUFFERED=1",
    }
    with patch.object(boris_guardian, "sh", side_effect=_fake(vals)):
        out=boris_guardian.backend_api_memory_guard()
    assert len(out["bad"]) == 2
    assert all("MALLOC_ARENA_MAX=2" in x for x in out["bad"])
