#!/usr/bin/env bash
# Bump the version, tag it, and push to trigger a GitHub Actions release build.
# Usage:
#   ./release.sh          # patch bump  (v0.1.0 -> v0.1.1)
#   ./release.sh minor    # minor bump  (v0.1.0 -> v0.2.0)
#   ./release.sh major    # major bump  (v0.1.0 -> v1.0.0)

set -euo pipefail

BUMP=${1:-patch}

latest=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
major=$(echo "$latest" | cut -d. -f1 | tr -d 'v')
minor=$(echo "$latest" | cut -d. -f2)
patch=$(echo "$latest" | cut -d. -f3)

case "$BUMP" in
  major) major=$((major + 1)); minor=0; patch=0 ;;
  minor) minor=$((minor + 1)); patch=0 ;;
  patch) patch=$((patch + 1)) ;;
  *) echo "Usage: $0 [major|minor|patch]"; exit 1 ;;
esac

new_tag="v${major}.${minor}.${patch}"

echo "Current version : $latest"
echo "New version     : $new_tag"
echo ""
read -rp "Proceed? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || exit 0

git tag "$new_tag"
git push origin "$new_tag"

echo ""
echo "Tag $new_tag pushed. GitHub Actions will build and publish the release."
