"""
secure_env.py — Variables de entorno protegidas con DPAPI de Windows.
El .env.dpapi solo puede descifrarse por el mismo usuario en la misma máquina.
Si el archivo es robado, es ilegible sin acceso a la cuenta de Windows.
"""
import os
import ctypes
import ctypes.wintypes

# ── DPAPI (Windows Data Protection API) ──────────────────────────────────────

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

_FLAG = 0x01  # CRYPTPROTECT_UI_FORBIDDEN — sin prompts de UI


def _encrypt(data: bytes) -> bytes:
    buf   = ctypes.create_string_buffer(data)
    b_in  = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    b_out = _BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(b_in), None, None, None, None, _FLAG, ctypes.byref(b_out)):
        raise ctypes.WinError()
    result = bytes(ctypes.string_at(b_out.pbData, b_out.cbData))
    ctypes.windll.kernel32.LocalFree(b_out.pbData)
    return result


def _decrypt(data: bytes) -> bytes:
    buf   = ctypes.create_string_buffer(data)
    b_in  = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    b_out = _BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(b_in), None, None, None, None, _FLAG, ctypes.byref(b_out)):
        raise ctypes.WinError()
    result = bytes(ctypes.string_at(b_out.pbData, b_out.cbData))
    ctypes.windll.kernel32.LocalFree(b_out.pbData)
    return result


# ── Rutas ─────────────────────────────────────────────────────────────────────

_HERE       = os.path.dirname(os.path.abspath(__file__))
_DPAPI_PATH = os.path.join(_HERE, ".env.dpapi")
_PLAIN_PATH = os.path.join(_HERE, ".env")


# ── API pública ───────────────────────────────────────────────────────────────

def load_secure_env() -> str:
    """
    Carga variables de entorno desde .env.dpapi (preferido) o .env (fallback).
    Retorna: 'dpapi' | 'plain' | 'none'
    """
    if os.path.exists(_DPAPI_PATH):
        with open(_DPAPI_PATH, "rb") as f:
            cipher = f.read()
        plain = _decrypt(cipher).decode("utf-8")
        _parse_and_set(plain)
        return "dpapi"
    if os.path.exists(_PLAIN_PATH):
        with open(_PLAIN_PATH, "r", encoding="utf-8") as f:
            plain = f.read()
        _parse_and_set(plain)
        return "plain"
    return "none"


def encrypt_env_file() -> int:
    """
    Encripta .env con DPAPI y lo guarda como .env.dpapi.
    Retorna el tamaño del archivo cifrado en bytes.
    """
    if not os.path.exists(_PLAIN_PATH):
        raise FileNotFoundError(f".env no encontrado en {_PLAIN_PATH}")
    with open(_PLAIN_PATH, "rb") as f:
        plain = f.read()
    cipher = _encrypt(plain)
    with open(_DPAPI_PATH, "wb") as f:
        f.write(cipher)
    return len(cipher)


def _parse_and_set(content: str) -> None:
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())
