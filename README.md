# Genesis

## Install

### On Ubuntu from a PPA

```bash
sudo add-apt-repository ppa:gjolly/genesis
sudo apt install -y python3-genesis
```

### Anywhere with a virtual env and pip

```bash
sudo apt install -y python3-pip python3-venv
python3 -m venv genesisvenv
genesisvenv/bin/pip install git+https://github.com/gjolly/genesis.git
genesisvenv/bin/genesis --help
```

## Usage

To build a QCOW2 (QEMU) Ubuntu 24.04 LTS image:

```bash
# Building a basic root filesystem
genesis debootstrap --output /tmp/noble-rootfs --series noble

# creating a disk image from this root filesystem
genesis create-disk --disk-image noble-disk.img --rootfs-dir /tmp/noble-rootfs

# Updating the image (at this point it only contains
# packages from the release pocket)
# Also install the ubuntu-server metapackage to get
# all the nice utilities
# And also install a kernel
genesis update-system \
    --disk-image noble-disk.img \
    --mirror "http://archive.ubuntu.com/ubuntu" \
    --series "noble" \
    --extra-package ubuntu-server \
    --extra-package linux-generic

# Finally, install grub
genesis install-grub --disk-image noble-disk.img
```

You can now convert this raw image to QCOW2 (you will need `qemu-utils`):

```bash
qemu-img convert -f raw -O qcow2 noble-disk.img noble-disk.qcow2

# and get rid of the raw image
rm noble-disk.img
```

To build a minimal QCOW2 Ubuntu 24.04 LTS image:

```bash
# Create basic root file system
genesis debootstrap --output /tmp/noble-rootfs --series noble

# Create the 4GB UEFI disk image containing our root file system
genesis create-disk --rootfs-dir /tmp/noble-rootfs --size 4 --disk-image noble-disk.img

# Update and install basic packages
genesis update-system \
    --disk-image noble-disk.img \
    --mirror "http://archive.ubuntu.com/ubuntu" \
    --series "noble" \
    --extra-package openssh-server \
    --extra-package apt-transport-https \
    --extra-package ca-certificates \
    --extra-package linux-generic

# Install grub
genesis install-grub --disk-image noble-disk.img

# Create a default user name "ubuntu" and add it in the sudo group
genesis create-user --disk-image /tmp/noble-disk.img --username ubuntu --sudo

# Add a SSH key for this user
genesis copy-files --disk-image /tmp/noble-disk.img --file /path/to/public/key:/home/ubuntu/.ssh/autorized_keys

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
genesis copy-files --disk-image /tmp/noble-disk.img --file /tmp/netplan.yaml:/etc/netplan/50-image.yaml
```
