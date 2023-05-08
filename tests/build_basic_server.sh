#!/bin/bash -eu

SERIES="lunar"
if [ -z ${TEST_DIR+x} ]; then
    TEST_DIR="$(mktemp -d /tmp/genesis-testXXXXXX)"
fi
MIRROR="${MIRROR:-http://archive.ubuntu.com/ubuntu}"
GENESIS_BIN="${GENESIS_BIN:-genesis}"
SCRIPT_DIR="$(dirname -- "$0")"

source "$SCRIPT_DIR/functions.sh"
trap cleanup EXIT

# Building a basic root filesystem
$GENESIS_BIN debootstrap --output "$TEST_DIR/$SERIES-rootfs" --series "$SERIES" --mirror "$MIRROR"

# creating a disk image from this root filesystem
$GENESIS_BIN create-disk --disk-image "$TEST_DIR/$SERIES-disk.img" --rootfs-dir "$TEST_DIR/$SERIES-rootfs"

# Updating the image (at this point it only contains
# packages from the release pocket)
# Also install the ubuntu-server metapackage to get
# all the nice utilities
# And also install a kernel (here we chose linux-kvm)
$GENESIS_BIN update-system \
    --disk-image "$TEST_DIR/$SERIES-disk.img" \
    --mirror "$MIRROR" \
    --series "$SERIES" \
    --extra-package ubuntu-server \
    --extra-package openssh-server \
    --extra-package cloud-init \
    --extra-package linux-kvm

# Finally, install grub
$GENESIS_BIN install-grub --disk-image "$TEST_DIR/$SERIES-disk.img"

# test copy file by configuring sources.list
cat > "$TEST_DIR/sources.list" << EOF
deb https://archive.ubuntu.com/ubuntu/ $SERIES main restricted universe
deb https://archive.ubuntu.com/ubuntu/ $SERIES-updates main restricted universe
deb https://archive.ubuntu.com/ubuntu/ $SERIES-security main restricted universe
EOF

$GENESIS_BIN copy-files --disk-image "$TEST_DIR/$SERIES-disk.img" \
  --file "$TEST_DIR/sources.list:/etc/apt/sources.list" --mod 664

start_x86_vm "$TEST_DIR/$SERIES-disk.img" raw

sleep 15

run_command "ls -la /"
kill_vm

trap - EXIT
rm -rf "$TEST_DIR"
