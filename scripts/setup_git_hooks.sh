#!/bin/bash
# Git hooks setup script for EduMind Backend
# Run this once to install all Git hooks

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "Setting up Git hooks for EduMind Backend"
echo "=========================================="

# Check if pre-commit is installed
if ! command -v pre-commit &> /dev/null; then
    echo ""
    echo "Installing pre-commit..."
    pip install pre-commit

    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to install pre-commit"
        echo "Try: pip install pre-commit"
        exit 1
    fi
fi

# Check if detect-secrets is installed (for baseline generation)
if ! pip show detect-secrets &> /dev/null; then
    echo ""
    echo "Installing detect-secrets for secret scanning..."
    pip install detect-secrets
fi

# Generate secrets baseline if it doesn't exist
if [ ! -f ".secrets.baseline" ]; then
    echo ""
    echo "Generating secrets baseline..."
    detect-secrets scan --baseline .secrets.baseline \
        --exclude '\.env$' \
        --exclude 'requirements.*\.txt$' \
        --exclude '\.secrets\.baseline$'
fi

# Install pre-commit hooks
echo ""
echo "Installing pre-commit hooks..."
pre-commit install
pre-commit install --hook-type commit-msg
pre-commit install --hook-type pre-push

# Update hooks if pre-commit config changed
echo ""
echo "Installing hook updates..."
pre-commit autoupdate

echo ""
echo "=========================================="
echo "Git hooks installed successfully!"
echo "=========================================="
echo ""
echo "Available hooks:"
echo "  - pre-commit:  Fast checks (formatting, linting, secrets)"
echo "  - commit-msg:   Conventional commits validation"
echo "  - pre-push:     Type checking (mypy)"
echo ""
echo "To skip hooks temporarily:"
echo "  git commit --no-verify -m 'message'"
echo "  git push --no-verify"
echo ""
echo "To run hooks manually:"
echo "  pre-commit run --all-files"
echo "  pre-commit run --all-files --hook-stage push"
echo ""
echo "To update hooks:"
echo "  pre-commit autoupdate"
echo "  pre-commit install --overwrite"
