"""CLI wrapper for the canonical owner-outreach volume safety module."""
import sys

sys.path.insert(0, "/root/BORIS/backend")

from app.services.owner_outreach_volume_safety import ensure, health

if __name__ == "__main__":
    print("OWNER_OUTREACH_DB_SAFETY", ensure())
    print("OWNER_OUTREACH_DB_SAFETY_HEALTH", health())
