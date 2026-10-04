#!/bin/bash

# Downloads the BIRD benchmark "financial" database (CC BY-SA 4.0, see NOTICE.md) into data/.
# Optional: the CLI also works on any SQLite file, e.g. the bundled demo database eval/data/shop.sqlite.
# Run from anywhere: it changes to the repository root first. Needs uv, curl, unzip and sqlite3. Downloads 346 MB.
set -e
cd "$(dirname "$0")/.."

echo "Downloading the BIRD financial database..."
echo ""

BIRD_DEV_URL="https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"
BIRD_DEV_SHA256="cdd6d19faeb45a23970b98d3ef6c40a87987c95459c2cf12076897a60cf5a630"

for cmd in uv curl unzip sqlite3; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: $cmd is not installed."
        if [ "$cmd" = "uv" ]; then
            echo "Please install uv first: https://github.com/astral-sh/uv"
            echo "Quick install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        fi
        exit 1
    fi
done

# checksum tool: macOS ships shasum, most Linux distros ship sha256sum
if command -v shasum &> /dev/null; then
    SHA_CMD="shasum -a 256"
elif command -v sha256sum &> /dev/null; then
    SHA_CMD="sha256sum"
else
    echo "Error: neither shasum nor sha256sum is installed."
    echo "Please install one (e.g. 'brew install perl' on macOS or 'sudo apt install coreutils' on Debian/Ubuntu)."
    exit 1
fi

mkdir -p data

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

echo "Downloading BIRD dev bundle (financial database subset)..."
curl -sL -o "$WORK_DIR/dev.zip" "$BIRD_DEV_URL"

ACTUAL_SHA256=$($SHA_CMD "$WORK_DIR/dev.zip" | awk '{print $1}')
if [ "$ACTUAL_SHA256" != "$BIRD_DEV_SHA256" ]; then
    echo "Error: dev.zip checksum mismatch."
    echo "Expected: $BIRD_DEV_SHA256"
    echo "Actual:   $ACTUAL_SHA256"
    exit 1
fi

unzip -q "$WORK_DIR/dev.zip" "dev_20240627/dev_databases.zip" -d "$WORK_DIR"
unzip -q "$WORK_DIR/dev_20240627/dev_databases.zip" "dev_databases/financial/*" -d "$WORK_DIR"

FINANCIAL_DIR="$WORK_DIR/dev_databases/financial"
if [ ! -f "$FINANCIAL_DIR/financial.sqlite" ]; then
    echo "Error: financial.sqlite not found in BIRD dev bundle"
    exit 1
fi

rm -rf data/financial.sqlite data/database_description
cp "$FINANCIAL_DIR/financial.sqlite" data/financial.sqlite
cp -R "$FINANCIAL_DIR/database_description" data/database_description

echo "Successfully installed data/financial.sqlite and data/database_description/"
echo ""

echo "Creating virtual environment and installing dependencies..."
uv sync
echo ""

echo "Done. Next: put FIREWORKS_API_KEY in .env, then run: uv run cli"
echo ""
