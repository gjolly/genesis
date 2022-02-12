#!/bin/bash

apt-get update
apt-get install -y \
    qemu-utils \
    qemu-system-x86 \
    debootstrap \
    kpartx
