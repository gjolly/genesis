import os
import sys
import shutil
import tempfile
from platform import processor
from typing import Dict, List

import click
import requests

import genesis.commands as commands
import genesis.disk_utils as disk_utils

SYSTEM_ROOT = os.open("/", os.O_RDONLY)
CWD = os.getcwd()


def run_deboostrap(series: str, bootstrap_mirror: str, build_dir_path: str) -> None:
    commands.run(["/usr/sbin/debootstrap", series, build_dir_path, bootstrap_mirror])


def install_extra_packages(packages: List[str]):
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    commands.run(["/usr/bin/apt-get", "update"])
    commands.run(["/usr/bin/apt-get", "install", "-y"] + packages)


def do_system_update():
    commands.run(["/usr/bin/apt-get", "update"])
    commands.run(["/usr/bin/apt-get", "-y", "upgrade"])


def exit_chroot():
    os.fchdir(SYSTEM_ROOT)
    os.chroot(".")

    os.chdir(CWD)


def verify_root():
    if os.geteuid() != 0:
        print("This command requires root privileges. Re-run with sudo.", file=sys.stderr)
        sys.exit(1)


def setup_loop_device(disk_image_path: str) -> str:
    out = commands.run_and_save_output(["losetup", "-P", "-f", "--show", disk_image_path])
    line_out = out.rstrip()
    loop_device = line_out.removeprefix("/dev/")
    return loop_device


def mount_partition(rootfs_partition: str, mount_dir: str) -> None:
    commands.run(["mount", rootfs_partition, mount_dir])


def add_fstab_entry(entry: str):
    f = open("/etc/fstab", "a")
    f.write(f"{entry}\n")
    f.close()


def umount_all(mount_dir: str):
    commands.run(["umount", "-R", mount_dir])


def teardown_loop_device(device: str):
    commands.run(["losetup", "-d", f"/dev/{device}"])


def divert_grub() -> None:
    commands.run(
        [
            "dpkg-divert",
            "--local",
            "--divert",
            "/etc/grub.d/30_os-prober.dpkg-divert",
            "--rename",
            "/etc/grub.d/30_os-prober",
        ]
    )

    detect_virt_tool = "/usr/bin/systemd-detect-virt"
    commands.run(["dpkg-divert", "--local", "--rename", detect_virt_tool])

    f = open(detect_virt_tool, "w")
    f.write("exit 1\n")
    f.close()

    commands.run(["chmod", "+x", detect_virt_tool])


def undivert_grub() -> None:
    commands.run(
        [
            "dpkg-divert",
            "--remove",
            "--local",
            "--divert",
            "/etc/grub.d/30_os-prober.dpkg-divert",
            "--rename",
            "/etc/grub.d/30_os-prober",
        ]
    )

    detect_virt_tool = "/usr/bin/systemd-detect-virt"
    os.remove(detect_virt_tool)
    commands.run(["dpkg-divert", "--remove", "--local", "--rename", detect_virt_tool])


def install_grub(device: str) -> None:
    """
    Install shim and grub and configure grub.
    This function will only work for amd64 and arm64.
    """
    packages = ["shim-signed"]

    # we only support legacy boot on x64
    if processor() == 'x86_64':
        packages.append("grub-pc")

    install_extra_packages(packages)

    efi_target = 'x86_64-efi'
    if processor() == "aarch64":
        efi_target = 'arm64-efi'

    commands.run(
        [
            "grub-install",
            device,
            "--boot-directory=/boot",
            "--efi-directory=/boot/efi",
            f"--target={efi_target}",
            "--uefi-secure-boot",
            "--no-nvram",
        ],
        cwd="/",
    )

    if processor() == 'x86_64':
        commands.run(["grub-install", "--target=i386-pc", device], cwd="/")

    divert_grub()
    commands.run(["update-grub"], cwd="/")

    undivert_grub()


def install_bootloader(bootloader: str, device: str) -> None:
    if bootloader == "grub":
        install_grub(device)
    else:
        raise ValueError(f"bootloader {bootloader} not supported")


def setup_source_list(mirror: str, series: str) -> None:
    f = open("/etc/apt/sources.list", "w")
    f.write(f"deb {mirror} {series} main\n")
    f.write(f"deb {mirror} {series}-updates main\n")
    f.write(f"deb {mirror} {series}-security main\n")

    f.close()


def mount_virtual_filesystems(mount_dir: str) -> None:
    commands.run(["mount", "dev-live", "-t", "devtmpfs", f"{mount_dir}/dev"])
    commands.run(["mount", "proc-live", "-t", "proc", f"{mount_dir}/proc"])
    commands.run(["mount", "sysfs-live", "-t", "sysfs", f"{mount_dir}/sys"])
    commands.run(["mount", "securityfs", "-t", "securityfs", f"{mount_dir}/sys/kernel/security"])
    commands.run(["mount", "-t", "cgroup2", "none", f"{mount_dir}/sys/fs/cgroup"])
    commands.run(["mount", "-t", "tmpfs", "none", f"{mount_dir}/tmp"])
    commands.run(["mount", "-t", "tmpfs", "none", f"{mount_dir}/var/lib/apt"])
    commands.run(["mount", "-t", "tmpfs", "none", f"{mount_dir}/var/cache/apt"])


def copy_directory(src: str, dest: str) -> None:
    commands.run(["cp", "-a", f"{src}/.", dest])


def copy_extra_files(mount_dir: str, files: Dict[str, str]) -> None:
    for dest, local in files.items():
        print(f"COPYING {local} -> {dest}")

        dest = f"{mount_dir}{dest}"

        directory = os.path.dirname(dest)
        commands.run(["mkdir", "-p", directory])

        shutil.copy(local, dest)


def download_file(url: str, path: str) -> None:
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)


def convert_binary_image(disk_image: str, binary_format: str, out_path: str) -> None:
    commands.run(
        [
            "qemu-img",
            "convert",
            "-f",
            "raw",
            "-O",
            binary_format,
            disk_image,
            out_path,
        ]
    )


class UEFIDisk:
    path: str
    loop_device: str
    esp_partition_number: int
    rootfs_partition_number: int

    @classmethod
    def create(cls, size: int):
        """
        Create an empty disk image file with the right partition layout.
        If a disk path is supplied, only attach loop devices (we assume the disk
        has already been setup).
        """
        disk = cls()
        disk.rootfs_partition_number = 1
        disk.esp_partition_number = 15

        disk.path = disk_utils.create_empty_disk(size)
        disk_utils.partition_uefi_disk(disk.path)
        disk.loop_device = setup_loop_device(disk.path)

        disk_utils.format_partition(disk.rootfs_map_device(), partition_format="ext4")
        disk_utils.format_partition(disk.esp_map_device(), partition_format="vfat", label="UEFI")

        return disk

    @classmethod
    def from_disk_image(cls, path: str):
        disk = UEFIDisk()
        disk.rootfs_partition_number = 1
        disk.esp_partition_number = 15
        disk.path = path
        disk.loop_device = setup_loop_device(disk.path)

        return disk

    def rootfs_map_device(self) -> str:
        return f"/dev/{self.loop_device}p{self.rootfs_partition_number}"

    def esp_map_device(self) -> str:
        return f"/dev/{self.loop_device}p{self.esp_partition_number}"


@click.group()
def cli() -> None:
    verify_root()
    pass


@cli.command()
@click.option("--output", type=str, default="rootfs", required=True)
@click.option("--series", type=str, required=True)
@click.option("--mirror", type=str, default="http://archive.ubuntu.com/ubuntu", required=True)
@click.option("--hostname", type=str, default="ubuntu", required=True)
def debootstrap(output: str, series: str, mirror: str, hostname: str):
    os.mkdir(output)
    run_deboostrap(series, mirror, output)

    f = open(f"{output}/etc/hostname", "w")
    f.write(hostname)


@cli.command()
@click.option("--rootfs-dir", type=str, default="rootfs", required=True)
@click.option("--disk-image", type=str, default="disk.img", required=True)
@click.option("--size", type=int, default=3, required=True)
def create_disk(rootfs_dir: str, disk_image: str, size: int):
    disk = UEFIDisk.create(size)
    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)

    copy_directory(rootfs_dir, mount_dir)
    os.mkdir(f"{mount_dir}/boot/efi")

    os.chroot(mount_dir)

    add_fstab_entry("LABEL=rootfs\t/\text4\tdefaults\t0\t1")
    add_fstab_entry("LABEL=UEFI\t/boot/efi\tvfat\tumask=0077\t0\t1")

    exit_chroot()

    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)

    shutil.move(disk.path, disk_image)


@cli.command()
@click.option("--disk-image", type=str, default="disk.img", required=True)
@click.option("--mirror", type=str, default="http://archive.ubuntu.com/ubuntu", required=True)
@click.option("--series", type=str, required=True)
@click.option("--extra-package", multiple=True)
def update_system(disk_image: str, mirror: str, series: str, extra_package: List[str]):
    disk = UEFIDisk.from_disk_image(disk_image)

    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)
    mount_partition(disk.esp_map_device(), f"{mount_dir}/boot/efi")
    mount_virtual_filesystems(mount_dir)

    os.chroot(mount_dir)

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"

    setup_source_list(mirror, series)
    do_system_update()

    install_extra_packages(list(extra_package))

    exit_chroot()
    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)


@cli.command()
@click.option("--disk-image", type=str, default="disk.img", required=True)
@click.option("--file", multiple=True)
@click.option("--owner", type=str, required=False)
@click.option("--mod", type=str, required=False)
def copy_files(disk_image: str, file: List[str], owner: str, mod: str):
    files = file
    disk = UEFIDisk.from_disk_image(disk_image)

    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)
    mount_partition(disk.esp_map_device(), f"{mount_dir}/boot/efi")
    mount_virtual_filesystems(mount_dir)

    file_map: Dict[str, str] = dict()
    for f in files:
        src, dst = f.split(":")
        file_map[dst] = src

    copy_extra_files(mount_dir, file_map)

    os.chroot(mount_dir)

    for dest in file_map:
        if owner is not None:
            shutil.chown(dest, owner, owner)
        if mod is not None:
            commands.run(["chmod", mod, dest])

    exit_chroot()

    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)


@cli.command()
@click.option("--disk-image", type=str, default="disk.img", required=True)
@click.option("--files", multiple=True)
def download_files(disk_image: str, files: List[str]):
    disk = UEFIDisk.from_disk_image(disk_image)

    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)
    mount_partition(disk.esp_map_device(), f"{mount_dir}/boot/efi")
    mount_virtual_filesystems(mount_dir)

    os.chroot(mount_dir)

    for file_url in files:
        path, url = file_url.split(":")
        download_file(url, path)

    exit_chroot()
    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)


@cli.command("install-grub")
@click.option("--disk-image", type=str, default="disk.img")
@click.option("--rootfs-label", type=str, default="rootfs")
def install_grub_command(disk_image: str, rootfs_label: str):
    disk = UEFIDisk.from_disk_image(disk_image)

    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)
    mount_partition(disk.esp_map_device(), f"{mount_dir}/boot/efi")
    mount_virtual_filesystems(mount_dir)

    grub_conf_url = "https://gist.githubusercontent.com/gjolly/14ed79fa5323a1d7a7f653f8dda60921/raw/8df1830c1ce6aa80b23515d9420c9afdc987ee1d/extra-grub-config.cfg"  # noqa

    grub_config_dir = f"{mount_dir}/etc/default/grub.d"
    if not os.path.exists(grub_config_dir):
        os.mkdir(grub_config_dir)

    download_file(grub_conf_url, f"{mount_dir}/etc/default/grub.d/extra-grub-config.cfg")

    os.chroot(mount_dir)

    install_bootloader("grub", f"/dev/{disk.loop_device}")

    exit_chroot()

    commands.run(
        [
            "sed",
            "-i",
            "-e",
            f"s,root=[^ ]*,root=LABEL={rootfs_label},",
            f"{mount_dir}/boot/grub/grub.cfg",
        ]
    )

    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)


@cli.command()
@click.option("--disk-image", type=str, default="disk.img")
@click.option("--package", multiple=True)
def install_packages(disk_image: str, package: List[str]):
    disk = UEFIDisk.from_disk_image(disk_image)

    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)
    mount_partition(disk.esp_map_device(), f"{mount_dir}/boot/efi")
    mount_virtual_filesystems(mount_dir)

    os.chroot(mount_dir)

    install_extra_packages(list(package))

    exit_chroot()

    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)


@cli.command()
@click.option("--disk-image", type=str, default="disk.img")
@click.option("--username", type=str, default="ubuntu")
@click.option("--ssh-key", type=str, required=False)
@click.option("--sudo/--no-sudo", default=False)
def create_user(disk_image: str, username: str, ssh_key: str, sudo: bool):
    disk = UEFIDisk.from_disk_image(disk_image)

    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)

    os.chroot(mount_dir)

    user_exists: bool = False
    with open("/etc/passwd") as passwd:
        lines = passwd.readlines()
        users = [line.split(":")[0] for line in lines]
        user_exists = username in users

    if not user_exists:
        commands.run(
            [
                "adduser",
                "--quiet",
                "--shell",
                "/bin/bash",
                "--gecos",
                "''",
                "--disabled-password",
                username,
            ]
        )

        # actually disable the password
        commands.run(["passwd", "--delete", username])

    if ssh_key is not None:
        # TODO: we should use path.join here
        home_dir = f"/home/{username}"
        commands.run(["mkdir", "-p", f"{home_dir}/.ssh"])

        ssh_key_file = f"{home_dir}/.ssh/authorized_keys"
        with open(ssh_key_file, "w") as key_file:
            key_file.write(ssh_key)

    if sudo:
        commands.run(["usermod", "-aG", "sudo", username])

    exit_chroot()

    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)


if __name__ == "__main__":
    cli()
