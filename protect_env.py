"""
protect_env.py — Encriptá el .env con DPAPI de Windows (ejecución única).

Uso: py protect_env.py

El archivo .env.dpapi resultante solo puede descifrarse por este usuario
en esta máquina. Si alguien roba el archivo, no puede leerlo.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secure_env import encrypt_env_file, _decrypt, _PLAIN_PATH, _DPAPI_PATH

print("AUTOBOT — Protección de credenciales con DPAPI de Windows")
print("=" * 56)

if not os.path.exists(_PLAIN_PATH):
    print("ERROR: No se encontró .env")
    sys.exit(1)

# Encriptar
size = encrypt_env_file()
print(f"  .env cifrado -> .env.dpapi ({size} bytes)")

# Verificar que la desencriptación es correcta
with open(_DPAPI_PATH, "rb") as f:
    cipher = f.read()
plain_back = _decrypt(cipher).decode("utf-8")
vars_found = [l.partition("=")[0].strip()
              for l in plain_back.splitlines()
              if "=" in l and not l.strip().startswith("#")]
print(f"  Verificación OK — variables cifradas: {', '.join(vars_found)}")
print()
print("El .env.dpapi solo puede ser descifrado por este usuario")
print("en esta máquina. En otra cuenta o máquina es ilegible.")
print()
print("Próximos pasos:")
print("  1. Verificá que el bot arranca correctamente: py bot.py")
print("  2. Si todo funciona, eliminá el .env original:")
print("       del .env")
print()
print("ADVERTENCIA: Si perdés acceso a esta cuenta de Windows,")
print("las keys del .env.dpapi son irrecuperables.")
print("Guardá las keys en otro lugar seguro (ej: gestor de contraseñas).")
