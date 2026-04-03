"""Shared pytest configuration for pje-download tests."""

import os


# Default env so imports don't fail on missing vars
os.environ.setdefault("MNI_USERNAME", "test_user")
os.environ.setdefault("MNI_PASSWORD", "test_pass")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("CONCURRENT_DOWNLOADS", "3")
# worker.py creates DOWNLOAD_BASE_DIR at module level — point to /tmp to avoid /data perms
os.environ.setdefault("DOWNLOAD_BASE_DIR", "/tmp/pje-test-downloads")
os.environ.setdefault("SESSION_STATE_PATH", "/tmp/pje-test-session.json")
