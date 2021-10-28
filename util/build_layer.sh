#!/bin/bash
set -o errexit -o pipefail -o noclobber -o nounset

# allow setting the python version by env var
set +o nounset
PYTHON_VERSION="${PYTHON_VERSION:-3.8}"
set -o nounset

# script constants
SECONDS=0
DIR="$(dirname "$(readlink -f "$0")")"
# util scripts need another layer of dirname
DIR="$(dirname "$DIR")"
USING_VENV=0
DIRS=(
  ./dist/upload/
  "./temp/build/python/lib/python${PYTHON_VERSION}/site-packages"
  ./temp/versions/
)
# shellcheck disable=SC2001
PYTHON_VERSION_REGEX="^Python $(echo "$PYTHON_VERSION" | sed 's/\./\\./')\.[[:digit:]]+"
PYTHON_BIN=
USING_WINPTY=0

# script functions

cleanup () {
  local ERR_CODE=$?
  popd > /dev/null

  if [ $ERR_CODE -ne 0 ]; then
    echo !! build failed
  fi

  if [ $USING_VENV -ne 0 ]; then
    echo :: automatically deactivating python venv
    deactivate || true
  fi

  exit $ERR_CODE
}

copy_file () {
  local SOURCE=$1
  local TARGET=$2

  echo :: copying file "$SOURCE" to "$TARGET"
  cp "$SOURCE" "$TARGET"
}

move_file () {
  local SOURCE=$1
  local TARGET=$2

  echo :: moving file "$SOURCE" to "$TARGET"
  mv "$SOURCE" "$TARGET"
}

create_dir () {
  local TARGET=$1

  echo :: creating dir "$TARGET"
  mkdir -p "$TARGET"
}

build_zip () {
  local SOURCE=$1
  local TARGET=$2
  local TARGET_DIR=
  local TARGET_FILE=
  local TARGET_REALDIR=

  echo :: building zip "$TARGET" from "$SOURCE" directory

  # need to rebuild the target because of directory traversal issues
  TARGET_DIR=$(dirname "$TARGET")
  TARGET_FILE=$(basename "$TARGET")
  TARGET_REALDIR=$(readlink -f "$TARGET_DIR")

  if [[ "$USING_WINPTY" -eq 1 ]]; then
    PYTHON_BIN="${PYTHON_BIN}.exe"
  fi
  "$PYTHON_BIN" "$DIR/util/build_zip.py" "$SOURCE" "$TARGET_REALDIR/$TARGET_FILE"
}

get_version () {
  local TARGET=$1
  local VERSION=

  VERSION=$(sha256sum "$TARGET" | cut -d' ' -f 1)
  echo "${VERSION:0:10}"
}

check_installed () {
  local BIN_NAME=$1

  echo :: checking if "$BIN_NAME" is installed
  command -v "$BIN_NAME" >/dev/null 2>&1
}

check_python_version () {
  local PYTHON_BIN=$1
  local ERR_CODE=

  if [[ "$USING_WINPTY" -eq 1 ]]; then
    PYTHON_BIN="${PYTHON_BIN}.exe"
  fi

  echo :: checking python version for "$PYTHON_BIN" executable

  "$PYTHON_BIN" --version | grep -E "$PYTHON_VERSION_REGEX" > /dev/null 2>&1
  ERR_CODE=$?

  if [ $ERR_CODE -ne 0 ]; then
    echo !! "$PYTHON_BIN" installation is incompatible, ignoring
  fi

  return $ERR_CODE
}

if [ -z "$1" ]; then
    echo "usage: $(basename "$0") <dep name> [dep name...]"
    exit 1
fi

pushd "$DIR" > /dev/null
trap cleanup EXIT
ERR_CODE=0

# build requirements checking

echo :: checking for valid python executable

# activate a venv if one exists first
if [ -d "./.venv" ]; then
  USING_VENV=1
  echo :: automatically activating python venv
  # shellcheck disable=SC1090
  source "$DIR/.venv/bin/activate"
fi

set +o errexit

# detect if we are running within git for windows, and if so
#   forcibly prefix python commands with "winpty" so that it runs python correctly
if check_installed "winpty"; then
  USING_WINPTY=1
fi

# try the most specific reference possible; the exact minor version of python we want
if check_installed "python${PYTHON_VERSION}"; then
  if check_python_version "python${PYTHON_VERSION}"; then
    PYTHON_BIN="python${PYTHON_VERSION}"
  fi
fi

# try "python3" next due to the number of systems that still default to python2
if [ "$PYTHON_BIN" = "" ]; then
  if check_installed "python3"; then
    if check_python_version "python3"; then
      PYTHON_BIN="python3"
    fi
  fi
fi

# try checking just "python" now
if [ "$PYTHON_BIN" = "" ]; then
  if check_installed "python"; then
    if check_python_version "python"; then
      PYTHON_BIN="python"
    fi
  fi
fi

# throw an error at this point and fail.
if [ "$PYTHON_BIN" = "" ]; then
  echo !! no compatible python installation found - python "$PYTHON_VERSION" is required.
  exit 1
fi

echo :: using "$PYTHON_BIN" installation for python

set -o errexit

# the meat of the build

set +o nounset
NAME=$1
set -o nounset

echo :: cleaning build directory if present
rm -rf ./temp/build || true

echo :: building layer \""$NAME"\" with deps \""$*"\"

for BUILD_DIR in "${DIRS[@]}"; do
  create_dir "$BUILD_DIR"
done

echo :: running pip install

PYTHON_CALL="$PYTHON_BIN"
if [[ "$USING_WINPTY" -eq 1 ]]; then
  PYTHON_CALL="${PYTHON_BIN}.exe"
fi

"$PYTHON_CALL" -m pip \
  install \
  --no-cache \
  --no-python-version-warning \
  --disable-pip-version-check \
  --progress-bar off \
  --quiet \
  --no-input \
  "$@" \
  -t "./temp/build/python/lib/python${PYTHON_VERSION}/site-packages"

TEMP_LAYER_ZIP_NAME="${NAME}-layer_python${PYTHON_VERSION}.zip"

build_zip ./temp/build/ "./dist/upload/$TEMP_LAYER_ZIP_NAME"

VERSION=$(get_version "./dist/upload/$TEMP_LAYER_ZIP_NAME")
echo :: determined version for layer to be "$VERSION"

echo "$VERSION" >| "./temp/versions/layer-${NAME}.version"
move_file "./dist/upload/$TEMP_LAYER_ZIP_NAME" "./dist/upload/${NAME}-layer_python${PYTHON_VERSION}_${VERSION}.zip"

echo :: cleaning up temp directory
rm -rf ./temp/build || true

echo :: layer build complete for "$NAME", took ~"$SECONDS" seconds
