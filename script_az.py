#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║        AZURE CONTAINER APPS — GESTOR DE CICLO DE VIDA (INTERACTIVO)       ║
║              Script multiplataforma (Windows · macOS · Linux)              ║
║                                                                          ║
║  Autor:   Luciano Mengarelli                                             ║
║  Versión: 2.1.0 (multiplataforma Windows · macOS · Linux)                 ║
║                                                                          ║
║  Descripción:                                                            ║
║    Script interactivo por consola que gestiona el ciclo de vida completo ║
║    de una aplicación Dockerizada en Azure Container Apps:                ║
║                                                                          ║
║      1) Build local + Push al ACR (con telemetría OCI)                   ║
║      2) Deploy en Azure Container Apps (perfil mínimo 0.25 CPU/0.5Gi)    ║
║      3) Parar / Iniciar, Listar y Eliminar instancias                    ║
║      4) Pull de la imagen desde el ACR                                   ║
║      5) Logs y telemetría en tiempo real                                 ║
║                                                                          ║
║  Requisitos:                                                             ║
║    - Docker Desktop en ejecución                                         ║
║    - Azure CLI instalado (`az` en PATH)                                  ║
║    - Python 3.8+                                                         ║
║    - Archivo .env con credenciales del Service Principal                 ║
║                                                                          ║
║  Uso:                                                                    ║
║    python deploy_azure.py                # usa .env del directorio        ║
║    python deploy_azure.py .env.prod      # .env personalizado             ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass  # Python < 3.7 sin reconfigure; los emojis podrían no imprimirse bien

# ---------------------------------------------------------------------------
# Habilitar secuencias ANSI en terminales de Windows antiguas
# (Windows Terminal ya las soporta nativamente; esto cubre cmd.exe clásico)
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        # Fallback universal: habilita VT processing en la mayoría de consolas
        os.system("")


# ---------------------------------------------------------------------------
# Resolución del ejecutable de Azure CLI
# ---------------------------------------------------------------------------
# En terminales abiertas ANTES de instalar Azure CLI (p. ej. VS Code), el PATH
# puede quedar desactualizado. Buscamos az en el PATH y, si no aparece, usamos
# la ruta de instalación estándar del MSI.
def _resolve_az() -> str:
    """
    Resuelve el ejecutable de Azure CLI.

    Orden de prioridad:
      1. Ruta de instalación estándar (MSI en Windows, Homebrew en macOS) —
         no depende del PATH de la terminal (evita PATHs desactualizados de
         terminales abiertas antes de la instalación).
      2. Búsqueda en el PATH (instalaciones en ubicaciones custom).
    """
    for candidate in (
        # Windows: instalación estándar del MSI
        r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
        r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
        # macOS: Homebrew (Apple Silicon / Intel)
        "/opt/homebrew/bin/az",
        "/usr/local/bin/az",
    ):
        if Path(candidate).exists():
            return candidate
    return shutil.which("az") or "az"


AZ_CMD = _resolve_az()


def _os_info() -> str:
    """Retorna una descripción corta del sistema operativo (Windows / macOS / Linux)."""
    if sys.platform == "win32":
        return f"Windows {platform.release()} ({platform.machine()})"
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    return f"{platform.system()} {platform.machine()}"


# ---------------------------------------------------------------------------
# Constantes de color para terminal (ANSI)
# ---------------------------------------------------------------------------
class Color:
    """Códigos ANSI para salida formateada en terminal."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"

    @staticmethod
    def ok(t: str) -> str:
        return f"{Color.GREEN}{t}{Color.RESET}"

    @staticmethod
    def warn(t: str) -> str:
        return f"{Color.YELLOW}{t}{Color.RESET}"

    @staticmethod
    def error(t: str) -> str:
        return f"{Color.RED}{t}{Color.RESET}"

    @staticmethod
    def info(t: str) -> str:
        return f"{Color.CYAN}{t}{Color.RESET}"

    @staticmethod
    def header(t: str) -> str:
        return f"{Color.BOLD}{Color.BLUE}{t}{Color.RESET}"

    @staticmethod
    def title(t: str) -> str:
        return f"{Color.BOLD}{Color.MAGENTA}{t}{Color.RESET}"

BANNER = r"""
   ___   _____   ___           _____            _        _
  / _ \ /  ___| / _ \         |  _  |          | |      (_)
 / /_\ \\ `--. / /_\ \______  | | | |_   _  ___| |_ _ __ _  ___
 |  _  | `--. \|  _  |______| | | | | | | |/ _ \ __| '__| |/ _ \
 | | | |/\__/ /| | | |        \ \_/ / |_| |  __/ |_| |  | |  __/
 \_| |_/\____/ \_| |_/         \___/ \__,_|\___|\__|_|  |_|\___|

   AZURE CONTAINER APPS — Gestor de Ciclo de Vida Interactivo
                WINDOWS · macOS · LINUX
"""


# ---------------------------------------------------------------------------
# Utilidades de logging
# ---------------------------------------------------------------------------
def log_ok(message: str) -> None:
    print(f"  {Color.ok('✓')} {message}")


def log_warn(message: str) -> None:
    print(f"  {Color.warn('⚠')} {message}")


def log_err(message: str) -> None:
    print(f"  {Color.error('✗')} {message}")


def log_info(message: str) -> None:
    print(f"  {Color.info('→')} {message}")


def log_detail(key: str, value: str) -> None:
    print(f"  {Color.DIM}{key}:{Color.RESET} {value}")


def section(title: str) -> None:
    """Imprime un separador de sección."""
    print(f"\n{Color.header('── ' + title + ' ' + '─' * max(6, 54 - len(title)))}")


def confirm(prompt: str) -> bool:
    """Pregunta sí/no al usuario. Devuelve True si responde 's'."""
    while True:
        answer = input(f"{Color.info('?')} {prompt} {Color.DIM}(s/n){Color.RESET}: ").strip().lower()
        if answer in ("s", "si", "sí", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print(f"  {Color.warn('Respondé con s o n')}")


# ---------------------------------------------------------------------------
# Ejecución de comandos del sistema
# ---------------------------------------------------------------------------
def run_cmd(cmd: List[str], capture: bool = True, timeout: Optional[int] = None) -> Tuple[int, str]:
    """
    Ejecuta un comando del sistema.

    Retorna (exit_code, stdout). Si el comando no existe, retorna (127, "").
    """
    if cmd and cmd[0] == "az":
        cmd = [AZ_CMD] + cmd[1:]
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip() if capture else ""
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""


def run_stream(cmd: List[str]) -> int:
    """
    Ejecuta un comando transmitiendo su salida en vivo a la consola
    (necesario para `--follow` de logs). Ctrl+C interrumpe el streaming.
    """
    if cmd and cmd[0] == "az":
        cmd = [AZ_CMD] + cmd[1:]
    try:
        return subprocess.run(cmd).returncode
    except KeyboardInterrupt:
        # El usuario cortó el streaming de logs — comportamiento normal
        print()
        return 130


# ---------------------------------------------------------------------------
# Carga del archivo .env
# ---------------------------------------------------------------------------
def load_env_file(env_path: str) -> dict:
    """
    Lee el archivo .env y retorna un diccionario de variables.

    Soporta comentarios (#), líneas vacías, KEY=VALUE y valores con comillas.
    """
    env_vars: dict = {}
    path = Path(env_path)

    if not path.exists():
        print(f"{Color.warn('⚠')} Archivo .env no encontrado: {env_path}")
        copy_cmd = "copy" if sys.platform == "win32" else "cp"
        print(f"  {Color.DIM}Copiá la plantilla: {copy_cmd} .env.example .env{Color.RESET}")
        return env_vars

    log_info(f"Cargando variables desde {path.absolute()}")

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                log_warn(f"Línea {line_num} ignorada (sin '='): {line[:50]}")
                continue

            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()

            # Quitar comillas simples o dobles
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]

            env_vars[key] = value

    return env_vars


# ---------------------------------------------------------------------------
# Configuración central
# ---------------------------------------------------------------------------
class Config:
    """
    Configuración del pipeline.

    - Credenciales del Service Principal → SIEMPRE desde el .env.
    - Infraestructura (RG, ACR, entorno, app) → se configura manualmente
      en cada ejecución vía Azure CLI (cada alumno tiene recursos distintos).
    - Valores de la imagen (nombre, tag, puerto) → defaults del .env,
      confirmables en el setup interactivo.
    """

    def __init__(self, env_vars: dict):
        def get(key: str, default: str) -> str:
            return env_vars.get(key) or os.environ.get(key) or default

        # --- Credenciales Service Principal (REQUERIDAS) ---
        self.client_id: str = get("AZURE_CLIENT_ID", "")
        self.client_secret: str = get("AZURE_CLIENT_SECRET", "")
        self.tenant_id: str = get("AZURE_TENANT_ID", "")
        self.subscription_id: str = get("AZURE_SUBSCRIPTION_ID", "")

        # --- Infraestructura Azure (se configura interactivamente vía Azure CLI) ---
        self.resource_group: str = get("RESOURCE_GROUP", "")
        self.acr_name: str = get("ACR_NAME", "")
        self.environment: str = get("CONTAINERAPP_ENVIRONMENT", "")

        # --- Nombre de la Container App ---
        # Default vacío (no "app-azure-instancia") a propósito: así, si ya
        # existe una app real en el resource group, el picker interactivo
        # la prioriza a ella en vez de tapar la lista con este default.
        self.app_name: str = get("CONTAINERAPP_NAME", "")

        # --- Imagen Docker ---
        self.image_name: str = get("IMAGE_NAME", "app-azure")
        self.tag: str = get("IMAGE_TAG", "v1.0.0")
        self.dockerfile: str = get("DOCKERFILE_PATH", "Dockerfile")
        self.build_context: str = get("BUILD_CONTEXT", ".")

        # --- Puerto del Dockerfile (ingress HTTP) ---
        self.target_port: str = get("TARGET_PORT", "3000")

        # --- Perfil de recursos mínimo (sin GPU, para no gastar créditos) ---
        self.cpu: str = get("CPU", "0.25")
        self.memory: str = get("MEMORY", "0.5Gi")
        self.min_replicas: str = get("MIN_REPLICAS", "1")
        self.max_replicas: str = get("MAX_REPLICAS", "1")

    # --- Propiedades derivadas ---
    @property
    def acr_server(self) -> str:
        return f"{self.acr_name}.azurecr.io"

    @property
    def full_image(self) -> str:
        return f"{self.acr_server}/{self.image_name}:{self.tag}"

    @property
    def latest_image(self) -> str:
        return f"{self.acr_server}/{self.image_name}:latest"

    def validate_credentials(self) -> List[str]:
        """Retorna la lista de errores de configuración (vacía si todo OK)."""
        errors = []
        required = {
            "AZURE_CLIENT_ID": self.client_id,
            "AZURE_CLIENT_SECRET": self.client_secret,
            "AZURE_TENANT_ID": self.tenant_id,
            "AZURE_SUBSCRIPTION_ID": self.subscription_id,
        }
        for key, value in required.items():
            if not value:
                errors.append(f"{key} está vacío — completalo en el .env")
        return errors

    def show(self) -> None:
        """Muestra la configuración actual (con secretos enmascarados)."""
        section("Configuración Actual")
        log_detail("Client ID", self.client_id or Color.warn("(vacío)"))
        log_detail("Client Secret", "••••••••" if self.client_secret else Color.warn("(vacío)"))
        log_detail("Tenant ID", self.tenant_id or Color.warn("(vacío)"))
        log_detail("Subscription", self.subscription_id or Color.warn("(vacío)"))
        log_detail("Resource Group", self.resource_group or Color.warn("(se elige al inicio)"))
        log_detail("ACR", self.acr_name or Color.warn("(se elige al inicio)"))
        log_detail("Environment ACA", self.environment or Color.warn("(se elige al inicio)"))
        log_detail("Container App", self.app_name)
        log_detail("Imagen", self.full_image)
        log_detail("Dockerfile", self.dockerfile)
        log_detail("Puerto (ingress)", self.target_port)
        log_detail("CPU / Memoria", f"{self.cpu} / {self.memory}  {Color.DIM}(mínimo, sin GPU){Color.RESET}")
        log_detail("Réplicas", f"min {self.min_replicas} / max {self.max_replicas}")


# ---------------------------------------------------------------------------
# Verificación del entorno (Windows / macOS)
# ---------------------------------------------------------------------------
class EnvironmentCheck:
    """Verifica que Docker y Azure CLI estén instalados y funcionando."""

    @staticmethod
    def check_docker() -> bool:
        log_info("Verificando Docker Desktop...")
        code, version = run_cmd(
            ["docker", "version", "--format", "{{.Server.Version}}"], timeout=15
        )
        if code == 127:
            log_err("Docker CLI no encontrado en PATH")
            install_url = (
                "https://docs.docker.com/desktop/setup/install/windows-install/"
                if sys.platform == "win32"
                else "https://docs.docker.com/desktop/setup/install/mac-install/"
            )
            print(f"    {install_url}")
            return False
        if code == 124:
            log_err("Timeout — Docker daemon no responde")
            return False
        if code != 0:
            log_err("Docker daemon no está accesible")
            print(f"    {Color.warn('¿Docker Desktop está iniciado? Arrancalo y reintentá.')}")
            return False
        log_ok(f"Docker Desktop en ejecución (Server v{version})")
        return True

    @staticmethod
    def check_azure_cli() -> bool:
        log_info("Verificando Azure CLI...")
        code, out = run_cmd(["az", "version"], timeout=15)
        if code == 127:
            log_err("Azure CLI no encontrado en PATH")
            log_detail("Ruta intentada", AZ_CMD)
            log_detail("PATH de la terminal", os.environ.get("PATH", "(vacío)"))
            install_url = (
                "https://docs.microsoft.com/cli/azure/install-azure-cli-windows"
                if sys.platform == "win32"
                else "https://docs.microsoft.com/cli/azure/install-azure-cli-macos"
            )
            print(f"    {install_url}")
            return False
        if code != 0:
            log_err("Azure CLI no funciona correctamente")
            return False
        try:
            version = json.loads(out).get("azure-cli", "instalada")
        except json.JSONDecodeError:
            version = "instalada"
        log_ok(f"Azure CLI {version}")
        return True


# ---------------------------------------------------------------------------
# Autenticación Azure (Service Principal)
# ---------------------------------------------------------------------------
class AzureAuth:
    """Login con Service Principal y autenticación contra el ACR."""

    def __init__(self, config: Config):
        self.config = config

    def login(self) -> bool:
        """
        Paso 1: `az login` con las credenciales del Service Principal.
        Paso 2: `az account set` para fijar la suscripción.
        """
        section("Autenticando en Azure (Service Principal)")

        log_detail("Client ID", self.config.client_id)
        log_detail("Tenant", self.config.tenant_id)

        log_info("az login --service-principal ...")
        code, _ = run_cmd(
            [
                "az", "login",
                "--service-principal",
                "--username", self.config.client_id,
                "--password", self.config.client_secret,
                "--tenant", self.config.tenant_id,
                "--output", "none",
            ],
            timeout=60,
        )
        if code != 0:
            log_err("Login con Service Principal falló — verificá AZURE_CLIENT_ID/SECRET/TENANT_ID")
            return False
        log_ok("Login Service Principal exitoso")

        log_info(f"Fijando suscripción {self.config.subscription_id} ...")
        code, _ = run_cmd(
            ["az", "account", "set", "--subscription", self.config.subscription_id],
            timeout=30,
        )
        if code != 0:
            log_err("No se pudo fijar la suscripción — verificá AZURE_SUBSCRIPTION_ID")
            return False
        log_ok("Suscripción activa")

        return True

    def acr_login(self) -> bool:
        """Autentica Docker contra el Azure Container Registry."""
        log_info(f"az acr login --name {self.config.acr_name} ...")
        code, _ = run_cmd(["az", "acr", "login", "--name", self.config.acr_name], timeout=60)
        if code != 0:
            log_err(f"No se pudo autenticar contra {self.config.acr_server}")
            print(f"    {Color.DIM}Verificá que el Service Principal tenga rol AcrPush o Contributor.{Color.RESET}")
            return False
        log_ok(f"Login exitoso en {self.config.acr_server}")
        return True

class InteractiveSetup:
    """
    Configuración interactiva de la infraestructura vía Azure CLI.

    Cada alumno tiene sus propios recursos (resource group, ACR, entorno de
    Container Apps), por eso la infraestructura NO va hardcodeada en el .env:
    el script lista los recursos reales de la cuenta con `az` y el usuario
    elige cuáles usar en esta ejecución. El .env solo aporta credenciales
    y valores por defecto.
    """

    @staticmethod
    def _az_list(cmd: List[str]) -> List[str]:
        """Ejecuta un query de az y retorna las líneas del resultado."""
        code, out = run_cmd(cmd, timeout=60)
        if code != 0 or not out:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    @staticmethod
    def ask(label: str, default: str) -> str:
        """Pregunta un valor libre con default (Enter lo acepta)."""
        try:
            value = input(
                f"  {Color.info('?')} {label} {Color.DIM}(Enter = {default}){Color.RESET}: "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(130)
        return value or default

    @classmethod
    def pick_from_list(cls, title: str, items: List[str], default: str = "",
                       custom_label: str = "escribir el nombre manualmente") -> str:
        """
        Muestra una lista numerada (obtenida desde az CLI) y pide elección:
          - número → selecciona el ítem de la lista
          - Enter  → usa el default
          - 0      → modo manual (escribir el nombre)
          - texto  → nombre manual directo
        """
        section(title)
        if items:
            for i, item in enumerate(items, 1):
                marker = "  ← default" if item == default else ""
                print(f"    {Color.BOLD}{i}{Color.RESET}. {item}{marker}")
            print(f"    {Color.DIM}0. {custom_label}{Color.RESET}")
        else:
            log_warn(f"No se encontraron recursos ({title}) — escribí el nombre manualmente")

        while True:
            try:
                prompt = f"  {Color.info('?')} Selección"
                if default:
                    prompt += f" {Color.DIM}(Enter = {default}){Color.RESET}"
                choice = input(f"{prompt}: ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                sys.exit(130)

            if not choice and default:
                return default
            if items and choice.isdigit() and int(choice) == 0:
                log_warn("Modo manual: escribí el nombre")
                continue
            if items and choice.isdigit() and 1 <= int(choice) <= len(items):
                return items[int(choice) - 1]
            if choice:
                return choice
            log_warn("Entrada vacía — elegí un número o escribí un nombre")

    @classmethod
    def configure_all(cls, config: Config) -> None:
        """
        Guía interactiva completa (todo vía az CLI):
        Resource Group → ACR → Entorno ACA → Container App → Imagen/Tag/Puerto
        """
        section("Configuración interactiva de infraestructura (vía Azure CLI)")
        log_info("Listando los recursos de tu cuenta con az...")

        # 1. Resource Group — lista de toda la suscripción
        rgs = cls._az_list(["az", "group", "list", "--query", "[].name", "-o", "tsv"])
        config.resource_group = cls.pick_from_list(
            "RESOURCE GROUP — listado de tu cuenta",
            rgs,
            default=config.resource_group or (rgs[0] if rgs else ""),
        )

        # 2. Azure Container Registry — lista de toda la suscripción
        acrs = cls._az_list(["az", "acr", "list", "--query", "[].name", "-o", "tsv"])
        config.acr_name = cls.pick_from_list(
            "AZURE CONTAINER REGISTRY — listado de tu cuenta",
            acrs,
            default=config.acr_name or (acrs[0] if acrs else ""),
        )

        # 3. Entorno de Container Apps — primero dentro del RG elegido
        envs = cls._az_list(
            ["az", "containerapp", "env", "list",
             "--resource-group", config.resource_group,
             "--query", "[].name", "-o", "tsv"]
        )
        if not envs:
            log_warn(f"Sin entornos en '{config.resource_group}' — listando toda la suscripción...")
            envs = cls._az_list(
                ["az", "containerapp", "env", "list", "--query", "[].name", "-o", "tsv"]
            )
        config.environment = cls.pick_from_list(
            "ENTORNO de Container Apps — listado de tu cuenta",
            envs,
            default=config.environment or (envs[0] if envs else ""),
        )

        # 4. Container App — muestra las existentes en el RG; permite crear nueva
        apps = cls._az_list(
            ["az", "containerapp", "list",
             "--resource-group", config.resource_group,
             "--query", "[].name", "-o", "tsv"]
        )
        config.app_name = cls.pick_from_list(
            f"CONTAINER APP a gestionar (existentes en '{config.resource_group}')",
            apps,
            default=config.app_name or (apps[0] if apps else "app-azure-instancia"),
            custom_label="crear una nueva (escribir el nombre)",
        )

        # 5. Parámetros de la imagen — defaults del proyecto, Enter acepta
        config.image_name = cls.ask(
            "Nombre de la imagen Docker", config.image_name or "app-azure"
        )
        config.tag = cls.ask("Tag / versión", config.tag or "v1.0.0")
        config.target_port = cls.ask(
            "Puerto donde escucha el Dockerfile", config.target_port or "3000"
        )

        # Resumen de lo elegido
        section("Infraestructura configurada")
        log_detail("Resource Group", config.resource_group)
        log_detail("ACR", f"{config.acr_name}.azurecr.io")
        log_detail("Entorno", config.environment)
        log_detail("Container App", config.app_name)
        log_detail("Imagen", config.full_image)
        log_detail("Puerto", config.target_port)


# ---------------------------------------------------------------------------
# Operaciones Docker (build con telemetría, push, pull)
# ---------------------------------------------------------------------------
class DockerOps:
    """Build, push y pull de la imagen Docker."""

    def __init__(self, config: Config):
        self.config = config

    # --- helpers de telemetría -------------------------------------------------
    @staticmethod
    def _git_sha() -> str:
        code, sha = run_cmd(["git", "rev-parse", "--short", "HEAD"], timeout=5)
        return sha if code == 0 else "unknown"

    @staticmethod
    def _git_url() -> str:
        code, url = run_cmd(["git", "remote", "get-url", "origin"], timeout=5)
        return url if code == 0 else "unknown"

    def build_with_telemetry(self) -> bool:
        """
        OP 1a: Build local de la imagen con telemetría embebida.

        La telemetría se inyecta de dos formas:
          - OCI Labels (org.opencontainers.image.*) — estándar de la industria
          - Build args (BUILD_DATE, VCS_REF, IMAGE_TAG) — disponibles para
            que el Dockerfile los grabe dentro de la app si lo desea.
        """
        section(f"Build local de imagen Docker — {self.config.full_image}")

        dockerfile = Path(self.config.dockerfile)
        if not dockerfile.exists():
            log_err(f"Dockerfile no encontrado: {dockerfile.absolute()}")
            return False

        now = datetime.now(timezone.utc).isoformat()
        sha = self._git_sha()
        url = self._git_url()

        cmd = [
            "docker", "build",
            # ACA solo ejecuta linux/amd64: fijamos la plataforma para que el
            # build en Mac Apple Silicon (arm64) produzca una imagen compatible
            # (Docker Desktop la emula — más lento, pero sirve para Azure).
            "--platform", "linux/amd64",
            "-f", str(dockerfile),
            "-t", self.config.full_image,
            "-t", self.config.latest_image,
            # --- Telemetría vía build-args ---
            "--build-arg", f"BUILD_DATE={now}",
            "--build-arg", f"VCS_REF={sha}",
            "--build-arg", f"IMAGE_TAG={self.config.tag}",
            # --- Telemetría vía OCI labels ---
            "--label", f"org.opencontainers.image.title={self.config.image_name}",
            "--label", f"org.opencontainers.image.version={self.config.tag}",
            "--label", f"org.opencontainers.image.created={now}",
            "--label", f"org.opencontainers.image.revision={sha}",
            "--label", f"org.opencontainers.image.source={url}",
            "--label", f"org.opencontainers.image.description=App {self.config.image_name} - Azure Container Apps",
            "--label", "author=ingenieria-um",
            self.config.build_context,
        ]

        log_detail("Contexto", str(Path(self.config.build_context).absolute()))
        log_detail("Plataforma", "linux/amd64 (requerida por Azure Container Apps)")
        log_info("docker build ... (este paso puede tardar varios minutos)")
        print(f"{Color.DIM}{'─' * 60}{Color.RESET}")

        code = run_stream(cmd)
        if code != 0:
            log_err(f"Build falló (código {code})")
            return False

        log_ok("Build completado")
        self._print_image_telemetry()
        return True

    def _print_image_telemetry(self) -> None:
        """Imprime la telemetría embebida en la imagen construida."""
        section("Telemetría de la imagen")
        code, size = run_cmd(
            ["docker", "image", "inspect", self.config.full_image,
             "--format", "{{.Size}}"], timeout=15
        )
        if code == 0 and size:
            size_mb = int(size) / 1024 / 1024
            log_detail("Tamaño", f"{size_mb:.2f} MB")

        code, created = run_cmd(
            ["docker", "image", "inspect", self.config.full_image,
             "--format", "{{.Created}}"], timeout=15
        )
        if code == 0:
            log_detail("Creada", created)

        code, digest = run_cmd(
            ["docker", "image", "inspect", self.config.full_image,
             "--format", "{{index .RepoDigests 0}}"], timeout=15
        )
        if code == 0 and digest:
            log_detail("Digest", digest)

        code, labels = run_cmd(
            ["docker", "image", "inspect", self.config.full_image,
             "--format", "{{json .Config.Labels}}"], timeout=15
        )
        if code == 0 and labels and labels != "null":
            try:
                for key, value in json.loads(labels).items():
                    if key.startswith("org.opencontainers"):
                        log_detail(key.split(".")[-1], value)
            except json.JSONDecodeError:
                pass

    def push(self) -> bool:
        """OP 1b: Push inmediato de la imagen al ACR (tag + latest)."""
        section(f"Push a Azure Container Registry — {self.config.acr_server}")

        for image in (self.config.full_image, self.config.latest_image):
            log_info(f"docker push {image}")
            code = run_stream(["docker", "push", image])
            if code != 0:
                log_err(f"Push falló (código {code})")
                return False
            log_ok(f"Imagen publicada: {image}")

        return True

    def verify_in_registry(self) -> bool:
        """
        Verificación post-push (requisito de la clase): confirma con az CLI
        que la imagen quedó en el ACR.

        Equivale a los comandos manuales:
          az acr repository show-tags --name <acr> --repository <imagen>
          az acr repository list --name <acr>
        """
        section("Verificación de la subida al ACR")

        code, out = run_cmd(
            ["az", "acr", "repository", "show-tags",
             "--name", self.config.acr_name,
             "--repository", self.config.image_name,
             "-o", "tsv"], timeout=60
        )
        if code == 0 and self.config.tag in out.split():
            log_ok(f"Verificado: {self.config.full_image} está en el ACR")
            log_detail("Tags en el repositorio", ", ".join(out.split()))
            return True

        # Fallback informativo: listar repositorios (comando de la clase)
        code2, repos = run_cmd(
            ["az", "acr", "repository", "list",
             "--name", self.config.acr_name, "-o", "tsv"], timeout=60
        )
        if code2 == 0:
            log_detail("Repositorios en el ACR", ", ".join(repos.split()) or "(ninguno)")
        log_warn(
            f"Verificación manual: az acr repository list --name {self.config.acr_name}"
        )
        return False

    def pull(self) -> bool:
        """OP 4: Pull de la imagen desde el ACR + ejecución local opcional."""
        section(f"Pull desde Azure Container Registry — {self.config.full_image}")

        if not auth.acr_login():
            return False

        log_info(f"docker pull {self.config.full_image}")
        code = run_stream(["docker", "pull", self.config.full_image])
        if code != 0:
            log_err(f"Pull falló (código {code})")
            return False
        log_ok("Imagen descargada localmente")

        # Telemetría post-pull
        code, size = run_cmd(
            ["docker", "image", "inspect", self.config.full_image,
             "--format", "{{.Size}}"], timeout=15
        )
        if code == 0 and size:
            log_detail("Tamaño local", f"{int(size) / 1024 / 1024:.2f} MB")

        # Ofrecer levantarla ahora: pull solo descarga la IMAGEN; el
        # contenedor aparece en Docker Desktop recién cuando se la ejecuta.
        if confirm("¿Ejecutar la imagen localmente ahora? (aparece en Docker Desktop → Containers)"):
            container_name = f"{self.config.image_name}-local"
            log_info(f"docker run -d --rm --name {container_name} "
                     f"-p {self.config.target_port}:{self.config.target_port} ...")
            code = run_stream([
                "docker", "run", "-d", "--rm",
                "--name", container_name,
                "-p", f"{self.config.target_port}:{self.config.target_port}",
                self.config.full_image,
            ])
            if code != 0:
                log_warn("No se pudo levantar el contenedor — usá el comando manual")
                return False
            log_ok(f"Contenedor '{container_name}' corriendo")
            print(f"  {Color.ok('🌐 LOCAL:')} {Color.BOLD}http://localhost:{self.config.target_port}{Color.RESET}")
            print(f"  {Color.DIM}Para frenarlo: docker stop {container_name} (con --rm se elimina solo){Color.RESET}")
            return True

        log_info("Podés ejecutarla manualmente con:")
        print(f"    {Color.DIM}docker run --rm -p {self.config.target_port}:{self.config.target_port} {self.config.full_image}{Color.RESET}")
        return True


# ---------------------------------------------------------------------------
# Operaciones Azure Container Apps
# ---------------------------------------------------------------------------
class ContainerAppsOps:
    """Crear/deploy, parar, iniciar, listar, eliminar y logs de la Container App."""

    def __init__(self, config: Config):
        self.config = config
        self._native_start_stop: Optional[bool] = None

    def exists(self) -> bool:
        """Verifica si la Container App ya existe."""
        code, _ = run_cmd(
            ["az", "containerapp", "show",
             "--name", self.config.app_name,
             "--resource-group", self.config.resource_group],
            timeout=30,
        )
        return code == 0

    def get_fqdn(self) -> str:
        """Obtiene el FQDN público de la Container App."""
        code, fqdn = run_cmd(
            ["az", "containerapp", "show",
             "--name", self.config.app_name,
             "--resource-group", self.config.resource_group,
             "--query", "properties.configuration.ingress.fqdn", "-o", "tsv"],
            timeout=30,
        )
        return fqdn if code == 0 else ""

    def deploy(self) -> bool:
        """
        OP 2: Deploy en Azure Container Apps.

        - Si la app NO existe → `az containerapp create` con:
            * Ingress externo (HTTP desde cualquier lugar) en el puerto del
              Dockerfile (8080).
            * Perfil mínimo de trabajo: 0.25 CPU / 0.5Gi RAM, sin GPU,
              1 réplica fija (min = max = 1).
            * Credenciales del ACR: SÍ O SÍ las del .env (Service Principal:
              AZURE_CLIENT_ID como usuario y AZURE_CLIENT_SECRET como password).
        - Si la app YA existe → `az containerapp registry set` con las
          credenciales del .env + `az containerapp update` con la nueva
          imagen y el mismo perfil de recursos.
        - Al final, si la app quedó detenida, la inicia para dejarla
          sirviendo e imprime la URL pública (FQDN).
        """
        section(f"Deploy en Azure Container Apps — {self.config.app_name}")

        # Verificar infraestructura antes de deployar
        if not self.config.resource_group or not self.config.acr_name \
                or not self.config.environment:
            log_err("Infraestructura incompleta (resource group / ACR / environment)")
            return False

        # Credenciales del ACR: las del .env (Service Principal), sí o sí.
        username = self.config.client_id
        password = self.config.client_secret

        if not self.exists():
            log_info(f"La app '{self.config.app_name}' no existe → CREAR")
            cmd = [
                "az", "containerapp", "create",
                "--name", self.config.app_name,
                "--resource-group", self.config.resource_group,
                "--environment", self.config.environment,
                "--image", self.config.full_image,
                "--registry-server", self.config.acr_server,
                "--registry-username", username,
                "--registry-password", password,
                # --- Ingress público HTTP en el puerto del Dockerfile ---
                "--ingress", "external",
                "--target-port", self.config.target_port,
                # --- Perfil mínimo de trabajo (sin GPU, créditos al mínimo) ---
                "--cpu", self.config.cpu,
                "--memory", self.config.memory,
                "--min-replicas", self.config.min_replicas,
                "--max-replicas", self.config.max_replicas,
            ]
        else:
            log_info(f"La app '{self.config.app_name}' ya existe → ACTUALIZAR")

            # `az containerapp update` no acepta --registry-*: las
            # credenciales del .env se aplican con `registry set` primero.
            log_info("az containerapp registry set ... (credenciales del .env)")
            code = run_stream([
                "az", "containerapp", "registry", "set",
                "--name", self.config.app_name,
                "--resource-group", self.config.resource_group,
                "--server", self.config.acr_server,
                "--username", username,
                "--password", password,
            ])
            if code != 0:
                log_err("No se pudieron aplicar las credenciales del ACR (.env)")
                return False

            cmd = [
                "az", "containerapp", "update",
                "--name", self.config.app_name,
                "--resource-group", self.config.resource_group,
                "--image", self.config.full_image,
                "--cpu", self.config.cpu,
                "--memory", self.config.memory,
                "--min-replicas", self.config.min_replicas,
                "--max-replicas", self.config.max_replicas,
            ]

        log_detail("Comando", " ".join(c for c in cmd if c != password))
        log_info("Ejecutando (puede tardar 1-2 minutos)...")
        code = run_stream(cmd)
        if code != 0:
            log_err(f"Deploy falló (código {code})")
            return False

        log_ok("Deploy completado")

        # create/update NO reanudan una app detenida con la acción stop:
        # la iniciamos para dejarla sirviendo.
        if self._get_running_status().lower() == "stopped":
            log_info("La app está detenida — iniciándola para dejarla sirviendo...")
            if not self._start_action():
                log_warn("No se pudo iniciar la app — probá la opción 3 → b")
            elif not self._wait_status("Running"):
                log_warn("Acción start aceptada — las réplicas levantan en ~30-60s")

        # Imprimir URL pública
        fqdn = self.get_fqdn()
        if fqdn:
            print()
            print(f"  {Color.ok('🌐 URL PÚBLICA:')} {Color.BOLD}https://{fqdn}{Color.RESET}")
            print(f"  {Color.DIM}(puerto interno {self.config.target_port} expuesto como HTTP/HTTPS){Color.RESET}")
            print()
        else:
            log_warn("No se pudo obtener el FQDN — consultalo con `az containerapp show`")

        return True

    def stop(self) -> bool:
        """
        OP 3a: Detener el servicio con la acción `stop` de Microsoft.App.

        - `az containerapp stop` si la CLI lo trae.
        - Si el comando no existe en la CLI instalada → la misma acción vía
          REST API con `az rest` (sigue siendo az CLI).
        NOTA: el viejo truco de escalar réplicas a 0 ya no es válido
        (`--max-replicas` debe estar en [1,1000]) y no es equivalente a stop.
        """
        section(f"Deteniendo servicio — {self.config.app_name}")

        if not self.exists():
            log_err(f"La app '{self.config.app_name}' no existe")
            return False

        if not confirm(f"¿Detener '{self.config.app_name}'?"):
            log_info("Operación cancelada")
            return True

        if self._native_start_stop_available():
            log_info("az containerapp stop ...")
            code = run_stream([
                "az", "containerapp", "stop",
                "--name", self.config.app_name,
                "--resource-group", self.config.resource_group,
            ])
        else:
            log_info("La CLI instalada no trae `containerapp stop` — "
                     "invocando la acción stop vía REST API (az rest)")
            uri = (
                f"https://management.azure.com/subscriptions/{self.config.subscription_id}"
                f"/resourceGroups/{self.config.resource_group}"
                f"/providers/Microsoft.App/containerApps/{self.config.app_name}"
                "/stop?api-version=2024-03-01"
            )
            log_detail("REST", f"POST .../containerApps/{self.config.app_name}/stop")
            code = run_stream(["az", "rest", "--method", "post", "--uri", uri, "--output", "none"])

        if code != 0:
            log_err("No se pudo detener la app")
            return False

        # Verificación real: esperar a que runningStatus sea Stopped
        if self._wait_status("Stopped"):
            log_ok("Servicio detenido — runningStatus: Stopped (no consume créditos)")
        else:
            log_warn("Acción aceptada por Azure — runningStatus aún no es Stopped")
        return True

    def _get_running_status(self) -> str:
        """Retorna properties.runningStatus de la app ('' si falla la consulta)."""
        code, status = run_cmd(
            ["az", "containerapp", "show",
             "--name", self.config.app_name,
             "--resource-group", self.config.resource_group,
             "--query", "properties.runningStatus", "-o", "tsv"],
            timeout=30,
        )
        return status if code == 0 else ""

    def _start_action(self) -> bool:
        """Ejecuta la acción start de la app (comando nativo o REST API).

        Retorna True si la acción fue aceptada por Azure.
        """
        if self._native_start_stop_available():
            log_info("az containerapp start ...")
            return run_stream([
                "az", "containerapp", "start",
                "--name", self.config.app_name,
                "--resource-group", self.config.resource_group,
            ]) == 0

        log_info("La CLI instalada no trae `containerapp start` — "
                 "invocando la acción start vía REST API (az rest)")
        uri = (
            f"https://management.azure.com/subscriptions/{self.config.subscription_id}"
            f"/resourceGroups/{self.config.resource_group}"
            f"/providers/Microsoft.App/containerApps/{self.config.app_name}"
            "/start?api-version=2024-03-01"
        )
        log_detail("REST", f"POST .../containerApps/{self.config.app_name}/start")
        return run_stream(["az", "rest", "--method", "post", "--uri", uri, "--output", "none"]) == 0

    def _native_start_stop_available(self) -> bool:
        """True si la CLI instalada trae `containerapp start/stop` (se consulta una sola vez)."""
        if self._native_start_stop is None:
            code, _ = run_cmd(["az", "containerapp", "start", "--help"], timeout=30)
            self._native_start_stop = code == 0
        return self._native_start_stop

    def _wait_status(self, expected: str, attempts: int = 12, delay: int = 5) -> bool:
        """Espera a que runningStatus sea `expected` (máximo attempts × delay segundos)."""
        for _ in range(attempts):
            if self._get_running_status().lower() == expected.lower():
                return True
            time.sleep(delay)
        return False

    def start(self) -> bool:
        """
        OP 3b: Iniciar el servicio detenido.

        - Si la app fue detenida con la acción stop (runningStatus "Stopped")
          → se reanuda con la acción `start`:
            1. `az containerapp start` si la CLI lo trae.
            2. Si el comando no existe en la CLI instalada → la misma acción
               vía REST API con `az rest` (sigue siendo az CLI, usa el login
               ya realizado).
          IMPORTANTE: `az containerapp update` NO reanuda una app "Stopped".
        - Si fue detenida escalando réplicas a 0 → se restauran las réplicas
          min/max del .env con `az containerapp update`.
        """
        section(f"Iniciando servicio — {self.config.app_name}")

        if not self.exists():
            log_err(f"La app '{self.config.app_name}' no existe")
            return False

        status = self._get_running_status()

        if status.lower() == "stopped":
            if not self._start_action():
                log_err("No se pudo iniciar la app")
                return False

            # Verificación real: esperar a que runningStatus sea Running
            if self._wait_status("Running"):
                log_ok("Servicio iniciado — runningStatus: Running")
            else:
                log_warn("Acción aceptada por Azure — las réplicas levantan en ~30-60s")
            return True

        # La app NO está "Stopped": fue detenida escalando réplicas a 0.
        # Restaurar réplicas min/max a los valores configurados.
        log_info(f"az containerapp update --min-replicas {self.config.min_replicas} "
                 f"--max-replicas {self.config.max_replicas} ...")
        code = run_stream([
            "az", "containerapp", "update",
            "--name", self.config.app_name,
            "--resource-group", self.config.resource_group,
            "--min-replicas", self.config.min_replicas,
            "--max-replicas", self.config.max_replicas,
        ])

        if code != 0:
            log_err("No se pudo iniciar la app")
            return False

        log_ok(f"Servicio iniciado — {self.config.min_replicas}/{self.config.max_replicas} réplicas")
        return True

    def list_apps(self) -> bool:
        """OP 3b: Lista todas las Container Apps del grupo de recursos en tabla."""
        section(f"Container Apps en el grupo '{self.config.resource_group}'")

        # Claves ASCII: con tildes (p. ej. "RéplicasMin") el query falla en
        # Windows por la codificación del salto az.cmd → cmd.exe.
        code, out = run_cmd([
            "az", "containerapp", "list",
            "--resource-group", self.config.resource_group,
            "--query",
            "[].{Name:name,"
            "Status:properties.runningStatus,"
            "CPU:properties.template.containers[0].resources.cpu,"
            "Memory:properties.template.containers[0].resources.memory,"
            "MinReplicas:properties.template.scale.minReplicas,"
            "FQDN:properties.configuration.ingress.fqdn}",
            "-o", "table",
        ], timeout=60)

        if code != 0:
            log_err("No se pudo listar las Container Apps")
            return False

        if not out:
            log_warn(f"No hay Container Apps en el grupo '{self.config.resource_group}'")
            return False

        print(out)
        return True

    def delete_app(self) -> bool:
        """
        OP 3c: Elimina una Container App elegida por el usuario.

        Primero lista las apps disponibles, pide el nombre por prompt y
        elimina con confirmación.
        """
        section("Eliminar Container App")

        # Listar nombres disponibles
        code, names_out = run_cmd([
            "az", "containerapp", "list",
            "--resource-group", self.config.resource_group,
            "--query", "[].name", "-o", "tsv",
        ], timeout=60)

        if code != 0 or not names_out:
            log_err("No se pudo obtener la lista de apps")
            return False

        names = [n.strip() for n in names_out.splitlines() if n.strip()]
        print(f"  {Color.DIM}Apps disponibles en '{self.config.resource_group}':{Color.RESET}")
        for i, name in enumerate(names, 1):
            marker = " ← actual" if name == self.config.app_name else ""
            print(f"    {i}. {Color.BOLD}{name}{Color.RESET}{marker}")

        # Prompt de selección
        try:
            selection = input(
                f"{Color.info('?')} Nombre de la app a eliminar "
                f"{Color.DIM}(Enter = cancelar){Color.RESET}: "
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return True

        if not selection:
            log_info("Eliminación cancelada")
            return True

        if selection not in names:
            log_err(f"'{selection}' no existe en el grupo '{self.config.resource_group}'")
            return False

        if not confirm(f"⚠ Esto es IRREVERSIBLE. ¿Eliminar '{selection}'?"):
            log_info("Eliminación cancelada")
            return True

        code = run_stream([
            "az", "containerapp", "delete",
            "--name", selection,
            "--resource-group", self.config.resource_group,
            "--yes",
        ])

        if code != 0:
            log_err("Fallo la eliminación")
            return False

        log_ok(f"'{selection}' eliminada correctamente")
        return True

    def stream_logs(self) -> bool:
        """
        OP 5: Transmisión en tiempo real de logs/trazas de la Container App.

        Usa `az containerapp logs show --follow` — el streaming queda activo
        hasta que el usuario presiona Ctrl+C (vuelve al menú principal).
        """
        section(f"Logs en tiempo real — {self.config.app_name}")
        log_info("Presioná Ctrl+C para volver al menú principal")
        print(f"{Color.DIM}{'─' * 60}{Color.RESET}")

        if not self.exists():
            log_err(f"La app '{self.config.app_name}' no existe")
            return False

        code = run_stream([
            "az", "containerapp", "logs", "show",
            "--name", self.config.app_name,
            "--resource-group", self.config.resource_group,
            "--type", "console",
            "--format", "text",
            "--follow",
        ])

        if code == 130:
            print(f"  {Color.info('→')} Streaming finalizado — volviendo al menú")
            return True
        if code != 0:
            log_err(f"No se pudo conectar a los logs (código {code})")
            return False
        return True


# ---------------------------------------------------------------------------
# Menú interactivo
# ---------------------------------------------------------------------------
def print_menu() -> None:
    """Imprime el menú principal de opciones."""
    print()
    print(f"  {Color.title('╔══════════════════════════════════════════════╗')}")
    print(f"  {Color.title('║   MENÚ PRINCIPAL — Azure Container Apps      ║')}")
    print(f"  {Color.title('╚══════════════════════════════════════════════╝')}")
    print()
    print(f"  {Color.BOLD}1{Color.RESET}) {Color.info('Construir y Subir')}   build local + push al ACR (telemetría)")
    print(f"  {Color.BOLD}2{Color.RESET}) {Color.info('Deploy')}             crear/actualizar Container App + URL pública")
    print(f"  {Color.BOLD}3{Color.RESET}) {Color.info('Parar / Iniciar / Listar / Eliminar')}   gestión de instancias")
    print(f"  {Color.BOLD}4{Color.RESET}) {Color.info('Pull')}               bajar imagen del ACR a la máquina local")
    print(f"  {Color.BOLD}5{Color.RESET}) {Color.info('Logs y Telemetría')}   streaming de trazas en tiempo real")
    print(f"  {Color.BOLD}6{Color.RESET}) {Color.info('Reconfigurar')}        volver a elegir infraestructura vía az CLI")
    print(f"  {Color.BOLD}0{Color.RESET}) {Color.error('Salir')}              (opción para cerrar sesión de az CLI)")
    print()


def manage_instances(ops: ContainerAppsOps) -> None:
    """Submenú de la Opción 3: Parar, Iniciar, Listar y Eliminar."""
    while True:
        print()
        print(f"  {Color.title('── Gestión de Instancias ──')}")
        print(f"  {Color.BOLD}a{Color.RESET}) {Color.info('Parar servicio')}    detener la app (no consume créditos)")
        print(f"  {Color.BOLD}b{Color.RESET}) {Color.info('Iniciar servicio')}  reanudar la app detenida")
        print(f"  {Color.BOLD}c{Color.RESET}) {Color.info('Listar instancias')}  tabla de apps del grupo de recursos")
        print(f"  {Color.BOLD}d{Color.RESET}) {Color.info('Eliminar instancia')}  prompt de selección + confirmación")
        print(f"  {Color.BOLD}0{Color.RESET}) {Color.error('Volver al menú principal')}")

        try:
            choice = input(f"\n  {Color.info('Selección')}: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return

        if choice == "a":
            ops.stop()
        elif choice == "b":
            ops.start()
        elif choice == "c":
            ops.list_apps()
        elif choice == "d":
            ops.delete_app()
        elif choice == "0":
            return
        else:
            log_warn("Opción inválida — usá a, b, c, d o 0")


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------
# Referencias globales compartidas entre las operaciones
config: Optional[Config] = None
auth: Optional[AzureAuth] = None


def main() -> None:
    global config, auth

    # --- Banner ---
    print(Color.header(BANNER))
    print(f"  {Color.DIM}Iniciado: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  |  {_os_info()}{Color.RESET}")

    # --- 1. Cargar .env (ruta opcional por CLI) ---
    env_path = sys.argv[1] if len(sys.argv) > 1 else ".env"
    env_vars = load_env_file(env_path)
    if not env_vars:
        log_err("Sin variables de configuración — copiá .env.example a .env y completalo")
        sys.exit(1)

    config = Config(env_vars)

    # --- 2. Validar credenciales del Service Principal ---
    errors = config.validate_credentials()
    if errors:
        print(f"\n{Color.error('✗ Errores de configuración:')}")
        for e in errors:
            print(f"    • {e}")
        print(f"  {Color.DIM}Los 4 valores salen de: az ad sp create-for-rbac --json-auth{Color.RESET}")
        sys.exit(1)

    # --- 3. Verificar entorno (Docker + Azure CLI) ---
    section("Verificación de entorno")
    env_check = EnvironmentCheck()
    if not env_check.check_docker():
        sys.exit(1)
    if not env_check.check_azure_cli():
        sys.exit(1)

    # --- 4. Login con Service Principal + configuración interactiva vía az CLI ---
    auth = AzureAuth(config)
    if not auth.login():
        sys.exit(1)
    InteractiveSetup.configure_all(config)

    # --- 5. Resumen de configuración ---
    config.show()

    # --- 6. Loop del menú principal ---
    docker_ops = DockerOps(config)
    aca_ops = ContainerAppsOps(config)

    while True:
        print_menu()
        try:
            choice = input(f"  {Color.info('Opción')}: ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{Color.warn('Sesión finalizada.')}")
            break

        if choice == "1":
            # Build local con telemetría + push inmediato al ACR + verificación
            if docker_ops.build_with_telemetry():
                if auth.acr_login():
                    if docker_ops.push():
                        docker_ops.verify_in_registry()
            else:
                log_warn("Build falló — push omitido")

        elif choice == "2":
            aca_ops.deploy()

        elif choice == "3":
            manage_instances(aca_ops)

        elif choice == "4":
            docker_ops.pull()

        elif choice == "5":
            aca_ops.stream_logs()

        elif choice == "6":
            InteractiveSetup.configure_all(config)

        elif choice == "0":
            if confirm("¿Cerrar la sesión de Azure CLI (az logout) antes de salir?"):
                log_info("az logout ...")
                code, _ = run_cmd(["az", "logout"], timeout=30)
                if code == 0:
                    log_ok("Sesión de Azure CLI cerrada")
                else:
                    log_warn("No se pudo cerrar la sesión (puede que ya estuviera cerrada)")
            print(f"\n{Color.ok('👋 ¡Hasta luego!')}\n")
            break

        else:
            log_warn("Opción inválida — elegí entre 0 y 6")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Color.warn('Script interrumpido por el usuario.')}")
        sys.exit(130)
    except Exception as e:
        print(f"\n{Color.error(f'Error inesperado: {e}')}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
