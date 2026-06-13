"""SMB share service — connection, read, and write operations."""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import smbclient

from config.share_drive import HICP_SHARE_DRIVE_CONFIG

logger = logging.getLogger(__name__)

_smb_registered = False


def connect_to_share() -> bool:
    """Register an SMB session (idempotent — safe to call multiple times)."""
    global _smb_registered
    if _smb_registered:
        return True
    try:
        domain = HICP_SHARE_DRIVE_CONFIG.get("Domain", "")
        user   = HICP_SHARE_DRIVE_CONFIG["user"]
        server = HICP_SHARE_DRIVE_CONFIG["serverName"]
        username = f"{domain}\\{user}" if domain else user

        try:
            smbclient.reset_connection_cache()
        except Exception:
            pass

        smbclient.register_session(
            server,
            username=username,
            password=HICP_SHARE_DRIVE_CONFIG["password"],
        )
        _smb_registered = True
        print(f"[SMB] Session registered for {server} as {username}")
        return True
    except Exception as exc:
        logger.error(f"[SMB] Session registration failed: {exc}")
        print(f"[SMB] Session registration failed: {exc}")
        return False


def write_file_to_share(file_path: str, content: bytes) -> bool:
    """Write *content* to the SMB share at *file_path* (relative to share root)."""
    if not connect_to_share():
        return False
    try:
        share_root = HICP_SHARE_DRIVE_CONFIG["shareName"]
        rel_path   = str(file_path).lstrip("\\/")
        unc_path   = f"{share_root}\\{rel_path}".replace("/", "\\")

        dir_path = unc_path.rsplit("\\", 1)[0]
        if dir_path != share_root:
            try:
                smbclient.makedirs(dir_path, exist_ok=True)
            except Exception as exc:
                logger.warning(f"[SMB] Could not create directory {dir_path}: {exc}")

        with smbclient.open_file(unc_path, mode="wb") as fh:
            fh.write(content)
        return True
    except Exception as exc:
        logger.error(f"[SMB] write_file_to_share failed: {exc}")
        return False


def read_image_from_share(img_url_path: str | None) -> str | None:
    """Read an image from the SMB share and return its base64-encoded content, or None."""
    if not img_url_path:
        return None
    connect_to_share()
    try:
        unc = img_url_path.replace("/", "\\")
        with smbclient.open_file(unc, mode="rb") as fh:
            return base64.b64encode(fh.read()).decode("utf-8")
    except Exception as exc:
        print(f"[SMB] Could not read image {img_url_path}: {exc}")
        return None


def path_exists_on_share(unc_path: str) -> bool:
    """Return True if the UNC path exists on the share."""
    connect_to_share()
    try:
        return smbclient.path.exists(unc_path)
    except Exception:
        return False
