"""SSH keypair generation for resource onboarding.

Generates ed25519 keypairs for Supavision to connect to monitored servers.
Keys are stored in ~/.ssh/ with 600 permissions. The public key is displayed
in the wizard for the user to copy to their server.
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

DEFAULT_KEY_PATH = "~/.ssh/supavision_ed25519"


def ensure_ssh_keypair(key_path: str | None = None) -> tuple[str, str]:
    """Ensure an SSH keypair exists at key_path. Generate if missing.

    Returns (resolved_private_key_path, public_key_content).
    """
    resolved = os.path.expanduser(key_path or DEFAULT_KEY_PATH)
    pub_path = resolved + ".pub"

    if not os.path.exists(resolved):
        # Ensure .ssh directory exists with correct permissions
        ssh_dir = os.path.dirname(resolved)
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

        logger.info("Generating SSH keypair at %s", resolved)
        subprocess.run(
            [
                "ssh-keygen",
                "-t", "ed25519",
                "-f", resolved,
                "-N", "",
                "-C", "supavision",
            ],
            check=True,
            capture_output=True,
        )
        os.chmod(resolved, 0o600)
        logger.info("SSH keypair generated successfully")

    if not os.path.exists(pub_path):
        raise FileNotFoundError(
            f"Private key exists at {resolved} but public key not found at {pub_path}"
        )

    with open(pub_path) as f:
        public_key = f.read().strip()

    return resolved, public_key
