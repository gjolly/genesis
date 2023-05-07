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
genesis create-disk --rootfs-dir /tmp/jammy-rootfs

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
