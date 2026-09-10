#!/usr/bin/env bash
set -euo pipefail
# Removes only dynamically allocated OpenCode workspace containers.
podman ps -a --format '{{.Names}}' | awk '/^oc-[0-9]+-[a-f0-9]+$/' | xargs -r podman rm -f
