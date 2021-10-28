#!/bin/bash
set -o errexit -o pipefail -o noclobber -o nounset

set +o nounset
PYTHON_VERSION="${PYTHON_VERSION:-3.8}"
set -o nounset

SECONDS=0
DIR="$(dirname "$(readlink -f "$0")")"
DIRS=(
  ./dist/upload/
  ./dist/cloudformation/
  ./dist/docs/assets/
  ./temp/build/
  ./temp/versions/
)

cleanup () {
  local ERR_CODE=$?
  popd > /dev/null

  if [ $ERR_CODE -ne 0 ]; then
    echo !! build failed
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

pushd "$DIR" > /dev/null
trap cleanup EXIT
ERR_CODE=0

set -o errexit

# the meat of the build

echo :: cleaning temp directory if present
rm -rf ./temp || true

echo :: cleaning dist directory if present
rm -rf ./dist || true

echo :: building Cheyenne

for BUILD_DIR in "${DIRS[@]}"; do
  create_dir "$BUILD_DIR"
done

# build AWS Lambda Layers
./util/build_layer.sh boto3
./util/build_layer.sh python-json-logger

# build AWS Lambda Functions
./util/build_function.sh duplicator
./util/build_function.sh ingest

# copy README, LICENSE, other useful files into dist
copy_file ./README.md ./dist/README.md
copy_file ./LICENSE ./dist/LICENSE
copy_file ./docs/INSTALL.md ./dist/docs/INSTALL.md

for _ASSET in "$DIR"/docs/assets/*.png; do
  ASSET=$(basename "$_ASSET")
  copy_file "./docs/assets/$ASSET" "./dist/docs/assets/$ASSET"
done

# copy cloudformation templates into dist
copy_file ./cloudformation/grant-source-invoke.yml ./dist/cloudformation/grant-source-invoke.yml
copy_file ./cloudformation/source-bucket.yml ./dist/cloudformation/source-bucket.yml
copy_file ./cloudformation/vault-account.yml ./dist/cloudformation/vault-account.yml
copy_file ./cloudformation/vault-init.yml ./dist/cloudformation/vault-init.yml

sed -i -E \
  -e "s/(Value: )''( # layer-boto3\.version)/\1'$(cat ./temp/versions/layer-boto3.version)'/" \
  -e "s/(Value: )''( # layer-python-json-logger\.version)/\1'$(cat ./temp/versions/layer-python-json-logger.version)'/" \
  -e "s/(Value: )''( # function-duplicator\.version)/\1'$(cat ./temp/versions/function-duplicator.version)'/" \
  -e "s/(Value: )''( # function-ingest\.version)/\1'$(cat ./temp/versions/function-ingest.version)'/" \
  ./dist/cloudformation/vault-init.yml

echo :: Cheyenne build complete, took ~"$SECONDS" seconds total
