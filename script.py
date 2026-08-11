#!/usr/bin/env python3
"""
script.py
Automatiza el flujo: docker tag -> docker push -> verificación en ACR.
Asume que la imagen local (app:version) ya fue creada previamente con 'docker build'.

Uso:
    python build.py --app app-azure --version v1.0.0
    python build.py --app app-azure --version v1.0.0 --auth admin --user MI_USER --password MI_PASS

Requisitos:
    - Docker instalado y corriendo (docker daemon activo)
    - La imagen local 'app:version' ya debe existir (docker images para chequear)
    - Azure CLI instalado (az) y logueado (az login) si usás --auth az (default)
"""

import argparse
import subprocess
import sys

ACR_NAME = "acringenieria"                       # nombre del registry (sin dominio)
ACR_LOGIN_SERVER = f"{ACR_NAME}.azurecr.io"       # dominio completo


def run(cmd, check=True):
    """Ejecuta un comando mostrando qué se está corriendo y su salida en vivo."""
    print(f"\n$ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if check and result.returncode != 0:
        print(f"\n❌ Falló el comando: {' '.join(cmd)}")
        sys.exit(result.returncode)
    return result.returncode


def login_az(acr_name):
    """Login usando az cli (requiere haber hecho 'az login' antes)."""
    run(["az", "acr", "login", "--name", acr_name])


def login_admin(login_server, user, password):
    """Login usando usuario/contraseña de tipo 'admin user' del ACR."""
    run(["docker", "login", login_server, "-u", user, "-p", password])


def tag_push(app_name, version, login_server):
    local_tag = f"{app_name}:{version}"
    remote_tag = f"{login_server}/{app_name}:{version}"

    # 1. Tag (asume que la imagen local ya existe, ej: creada con 'docker build')
    run(["docker", "tag", local_tag, remote_tag])

    # 2. Push
    run(["docker", "push", remote_tag])

    return remote_tag


def verify_upload(acr_name, app_name):
    print(f"\n🔍 Verificando repos en {acr_name}...")
    run(["az", "acr", "repository", "list", "--name", acr_name, "--output", "table"])

    print(f"\n🔍 Verificando tags de '{app_name}'...")
    run([
        "az", "acr", "repository", "show-tags",
        "--name", acr_name,
        "--repository", app_name,
        "--output", "table",
    ], check=False)  # check=False por si el repo aún no tenía tags previos


def main():
    parser = argparse.ArgumentParser(description="Build, tag y push de una imagen Docker a Azure ACR")
    parser.add_argument("--app", required=True, help="Nombre de la app/imagen, ej: app-azure")
    parser.add_argument("--version", required=True, help="Tag de versión, ej: v1.0.0 (debe coincidir con la imagen local ya creada)")
    parser.add_argument("--acr", default=ACR_NAME, help=f"Nombre del ACR (default: {ACR_NAME})")
    parser.add_argument(
        "--auth", choices=["az", "admin"], default="az",
        help="Método de login: 'az' usa az cli (default), 'admin' usa usuario/contraseña del ACR",
    )
    parser.add_argument("--user", help="Usuario del ACR (solo si --auth admin)")
    parser.add_argument("--password", help="Password del ACR (solo si --auth admin)")

    args = parser.parse_args()
    login_server = f"{args.acr}.azurecr.io"

    print(f"=== Subiendo {args.app}:{args.version} a {login_server} ===")
    print("(Se asume que la imagen local ya existe, creada con 'docker build')")

    # Login
    if args.auth == "az":
        login_az(args.acr)
    else:
        if not args.user or not args.password:
            print("❌ Para --auth admin necesitás pasar --user y --password")
            sys.exit(1)
        login_admin(login_server, args.user, args.password)

    # Tag + push
    remote_tag = tag_push(args.app, args.version, login_server)

    # Verificación
    verify_upload(args.acr, args.app)

    print(f"\n✅ Listo! Imagen disponible en: {remote_tag}")


if __name__ == "__main__":
    main()