# Test package bootstrap. Never let unit/safety QA pollute owner-facing production audit telemetry.
import os
os.environ.setdefault("BORIS_TEST_MODE", "1")
