#!/bin/bash
#SBATCH --job-name=heldout_net
#SBATCH --partition=serc
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --array=1-12%3
#SBATCH --output=logs/%x_%A_%a.out
#SBATCH --error=logs/%x_%A_%a.err

# The Python entry point independently verifies the post-push release artifact.
set -euo pipefail
ml gcc/12.4.0
PROJECT_ROOT=/home/groups/ettore88/nberrios/safod_das_git/notebooks
PROJECT_ROOT=$PROJECT_ROOT/faultzone/repeaters_v2
DAS_PYTHON=/home/users/nberrios/miniconda3/envs/das/bin/python
cd "$PROJECT_ROOT"
INTERVAL_ID=$(printf 'heldout_%02d' "$SLURM_ARRAY_TASK_ID")
"$DAS_PYTHON" -u -m src.run_heldout_network_interval \
    --interval-id "$INTERVAL_ID"
