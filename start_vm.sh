#!/bin/bash -eux

FILE="$1"

if [ -z "$FILE" ]; then
  echo "Usage: $0 IMAGE_FILE"
fi

userdata=$(mktemp --suffix='.yaml')
seed=$(mktemp --suffix='.img')

cleanup() {
  rm -f "$seed" "$userdata"
}

trap cleanup EXIT

cat << EOF > "$userdata"
#cloud-config
ssh_import_id:
  - gh:gjolly
EOF

cloud-localds "$seed" "$userdata"

qemu-system-x86_64 \
  -nographic \
  -snapshot \
  -cpu host \
  -enable-kvm \
  -smp 4 \
  -m 4G \
  -drive if=virtio,format=raw,file="${FILE}" \
  -drive if=virtio,format=raw,file="$seed" \
  -device virtio-net-pci,netdev=net0 --netdev user,id=net0,hostfwd=tcp::2222-:22 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd
