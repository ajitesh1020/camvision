#!/bin/bash
# Launch the CamVision GUI beside the AXIS GUI (called from [APPLICATIONS] APP).
#
# Resolves the repository root (two levels up from this config dir), puts the
# camvision package on PYTHONPATH, and stores/reads config.json in this config
# dir so calibration and offsets live with the machine config.
set -e
CONFIG_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$CONFIG_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:$PYTHONPATH"
exec python3 -m camvision.app "$CONFIG_DIR/config.json"
