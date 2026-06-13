"""SMB share drive configuration."""
import os

HICP_SHARE_DRIVE_CONFIG = {
    "user":       os.getenv("SMB_USER",     ""),
    "password":   os.getenv("SMB_PASSWORD", ""),
    "serverName": os.getenv("SMB_SERVER",   ""),
    "shareName":  os.getenv("SMB_SHARE",    r""),
    "Domain":     os.getenv("SMB_DOMAIN",   ""),
}
