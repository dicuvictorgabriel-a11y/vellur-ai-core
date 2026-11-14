"""The login system is a protected subsystem (owner decision, 2026-11-14): app/auth.py must not change without the
owner's explicit approval. If you really have approval, update the hash below in the same commit."""
import hashlib
import os

AUTH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "auth.py")


def test_auth_module_is_unchanged():
    assert hashlib.sha256(open(AUTH, "rb").read()).hexdigest() == "9c46d8a89c90452ffadf95f88aa71286bca1647b2958c49e40b102e05bd0947c"
