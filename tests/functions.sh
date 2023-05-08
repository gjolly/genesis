TEST_DIR="${TEST_DIR:-/tmp/genesis-test}"

create_cloud_init_seed() {
    rm -f "$TEST_DIR/genesis-test-key" "$TEST_DIR/genesis-test-key.pub"
    ssh-keygen -f "$TEST_DIR/genesis-test-key" -t rsa -C genesis-test -P ''

    cat > "$TEST_DIR/genesis-test-cloudinit" << EOF
#cloud-config
ssh_authorized_keys:
  - $(cat "$TEST_DIR/genesis-test-key.pub")
EOF
    cloud-localds "$TEST_DIR/genesis-test-seed.img" "$TEST_DIR/genesis-test-cloudinit"

    rm "$TEST_DIR/genesis-test-cloudinit"
}

start_x86_vm() {
    image="${1}"
    format="${2}"

    if [ -z "$image" ] || [ -z "$format" ]; then
        echo "Usage: start_x86_vm IMAGE IMAGE_FORMAT" >&2
        exit 1
    fi

    echo "Creating new vanilla EFI vars" >&2
    EFI_VARS="$TEST_DIR/EFI_VARS.fd"
    cp /usr/share/OVMF/OVMF_VARS.fd $EFI_VARS

    echo "Creating new cloud-init config" >&2
    create_cloud_init_seed

    # basic machine config
    params="-cpu host -machine type=q35,accel=kvm -m 2048"

    # run as a daemon
    params="$params -daemonize -pidfile $TEST_DIR/genesis-test-qemu-pid"

    # we don't want graphic nor modify the actual disk
    params="$params -snapshot"

    # networking
    params="$params -netdev id=net00,type=user,hostfwd=tcp::2222-:22"
    params="$params -device virtio-net-pci,netdev=net00"

    # disks
    params="$params -drive if=virtio,format=$format,file=$image"
    params="$params -drive if=virtio,format=raw,file=$TEST_DIR/genesis-test-seed.img"

    # UEFI bios
    params="$params -drive if=pflash,format=raw,unit=0,file=/usr/share/OVMF/OVMF_CODE.fd,readonly=on"
    params="$params -drive if=pflash,format=raw,unit=1,file=$EFI_VARS"

    echo "Starting VM" >&2
    eval qemu-system-x86_64 $params
}

kill_vm() {
    kill "$(cat $TEST_DIR/genesis-test-qemu-pid)"
}

run_command() {
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "$TEST_DIR/genesis-test-key" ubuntu@0.0.0.0 -p 2222 $1
}

cleanup() {
    echo "FAILED!" >&2
    echo "Test dir ($TEST_DIR) left intact for investigations" >&2
    echo "Once done, remove the directory: sudo rm -r $TEST_DIR" >&2
}
