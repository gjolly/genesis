# Genesis

## Install

Install from PPA:

```bash
curl -L 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xe8de5c81c12b06fe3fc4e35114aaaf80565cd7fb' | \
    gpg --dearmor | \
    sudo tee /etc/apt/keyrings/genesis-ppa.gpg > /dev/null
echo 'deb [signed-by=/etc/apt/keyrings/genesis-ppa.gpg] https://ppa.launchpadcontent.net/gjolly/genesis/ubuntu lunar main' | \
    sudo tee /etc/apt/sources.list.d/genesis.list
sudo apt-get update
sudo apt-get -y install python3-genesis
```

## Usage

To build a QCOW2 (QEMU) Ubuntu 22.04 LTS image:

```bash
# Building a basic root filesystem
genesis debootstrap --output /tmp/jammy-rootfs --series jammy

# creating a disk image from this root filesystem
genesis create-disk --disk-image jammy-disk.img --rootfs-dir /tmp/jammy-rootfs

# Updating the image (at this point it only contains
# packages from the release pocket)
# Also install the ubuntu-server metapackage to get
# all the nice utilities
# And also install a kernel (here we chose linux-kvm)
genesis update-system \
    --disk-image jammy-disk.img \
    --mirror "http://archive.ubuntu.com/ubuntu" \
    --series "jammy" \
    --extra-package ubuntu-server \
    --extra-package linux-kvm

# Finally, install grub
genesis install-grub --disk-image jammy-disk.img
```

You can now convert this raw image to QCOW2 (you will need `qemu-utils`):

```bash
qemu-img convert -f raw -O qcow2 jammy-disk.img jammy-disk.qcow2

# and get rid of the raw image
rm jammy-disk.img
```

To build a minimal QCOW2 Ubuntu 23.04 image:

```bash
# Create basic root file system
genesis debootstrap --output /tmp/lunar-rootfs --series lunar

# Create the 4GB UEFI disk image containing our root file system
genesis create-disk --rootfs-dir /tmp/lunar-rootfs --size 4 --disk-image lunar-disk.img

# Update and install basic packages
genesis update-system \
    --disk-image lunar-disk.img \
    --mirror "http://archive.ubuntu.com/ubuntu" \
    --series "lunar" \
    --extra-package openssh-server --extra-package apt-transport-https --extra-package ca-certificates --extra-package linux-kvm

# Install grub
genesis install-grub --disk-image lunar-disk.img

# Create a default user name "ubuntu" and add it in the sudo group
genesis create-user --disk-image /tmp/lunar-disk.img --username ubuntu --sudo

# Add a SSH key for this user
genesis copy-files --disk-image /tmp/lunar-disk.img --file /path/to/public/key:/home/ubuntu/.ssh/autorized_keys

# Configure networking
cat > /etc/netplan/50-image.yaml << EOF
network:
    version: 2
    ethernets:
        eth0:
            dhcp4: true
            match:
                driver: virtio_net
            set-name: eth0
EOF
genesis copy-files --disk-image /tmp/lunar-disk.img --file /tmp/netplan.yaml:/etc/netplan/50-image.yaml
```
