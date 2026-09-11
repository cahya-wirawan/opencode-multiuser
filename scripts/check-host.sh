#!/usr/bin/env bash
set -euo pipefail
uid=$(id -u)
runtime=${XDG_RUNTIME_DIR:-/run/user/$uid}
controllers="/sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service/cgroup.controllers"

echo "user: $(id -un) (uid=$uid)"
echo "runtime: $runtime"
[[ -d "$runtime" ]] && echo "runtime dir: OK" || echo "runtime dir: MISSING"
[[ -S "$runtime/bus" ]] && echo "user bus: OK" || echo "user bus: MISSING"
if [[ -r "$controllers" ]]; then
  echo "cgroup controllers: $(cat "$controllers")"
else
  echo "cgroup controllers: unavailable ($controllers)"
fi
podman info --format 'podman: cgroupVersion={{.Host.CgroupsVersion}} cgroupManager={{.Host.CgroupManager}} runtime={{.Host.OCIRuntime.Name}}' 2>/dev/null || true
