import os
import sys
import shutil
import tempfile
from glob import glob
from platform import processor
from typing import Dict, List

import click
import requests

import genesis.commands as commands
import genesis.disk_utils as disk_utils


NAMESERVER = "1.1.1.1"


def run_deboostrap(series: str, bootstrap_mirror: str, build_dir_path: str) -> None:
    commands.run(["/usr/sbin/debootstrap", series, build_dir_path, bootstrap_mirror])


def setup_apt_cache(directory: str, apt_cache: str) -> None:
    cache_config = os.path.join(directory, "etc/apt/apt.conf.d/00aptproxy")
    with open(cache_config, "w") as f:
        f.write(f'Acquire::http::Proxy "{apt_cache}";\n')


def remove_apt_cache(directory: str) -> None:
    cache_config = os.path.join(directory, "etc/apt/apt.conf.d/00aptproxy")
    if os.path.exists(cache_config):
        os.remove(cache_config)


def install_extra_packages(device: str, directory: str, packages: List[str]):
    environment = {
        "DEBIAN_FRONTEND": "noninteractive",
    }
    bind_devices = glob(f"{device}*")

    commands.run_with_nspawn(
        directory, ["/usr/bin/apt", "update"], environment=environment
    )
    commands.run_with_nspawn(
        directory,
        ["/usr/bin/apt", "install", "-y"] + packages,
        environment=environment,
        bind_devices=bind_devices,
    )


def do_system_update(device: str, directory: str) -> None:
    environment = {
        "DEBIAN_FRONTEND": "noninteractive",
    }
    bind_devices = glob(f"{device}*")

    commands.run_with_nspawn(
        directory, ["/usr/bin/apt", "update"], environment=environment
    )
    commands.run_with_nspawn(
        directory,
        ["/usr/bin/apt", "-y", "full-upgrade"],
        environment=environment,
        bind_devices=bind_devices,
    )


def replace_resolv_conf(directory: str, nameserver: str) -> str:
    resolv_conf = os.path.join(directory, "etc/resolv.conf")
    saved_resolvconf_fd, saved_resolvconf = tempfile.mkstemp(
        prefix="resolv", suffix=".conf"
    )
    os.close(saved_resolvconf_fd)
    if os.path.exists(resolv_conf):
        commands.run(["mv", resolv_conf, saved_resolvconf])

    with open(resolv_conf, "w") as f:
        f.write(f"nameserver {nameserver}\n")

    return saved_resolvconf


def restore_resolv_conf(directory: str, saved_resolvconf: str) -> None:
    resolv_conf = os.path.join(directory, "etc/resolv.conf")
    commands.run(["mv", saved_resolvconf, resolv_conf])


def verify_root():
    if os.geteuid() != 0:
        print(
            "This command requires root privileges. Re-run with sudo.", file=sys.stderr
        )
        sys.exit(1)


def setup_loop_device(disk_image_path: str) -> str:
    out = commands.run_and_save_output(
        ["losetup", "-P", "-f", "--show", disk_image_path]
    )
    line_out = out.rstrip()
    loop_device = line_out.removeprefix("/dev/")
    return loop_device


def mount_partition(rootfs_partition: str, mount_dir: str) -> None:
    commands.run(["mount", rootfs_partition, mount_dir])


def add_fstab_entry(mount_dir: str, entry: str):
    f = open(f"{mount_dir}/etc/fstab", "a")
    f.write(f"{entry}\n")
    f.close()


def umount_all(mount_dir: str):
    commands.run(["umount", "-R", mount_dir])


def teardown_loop_device(device: str):
    commands.run(["losetup", "-d", f"/dev/{device}"])


def divert_grub(directory: str) -> None:
    commands.run_with_nspawn(
        directory,
        [
            "dpkg-divert",
            "--local",
            "--divert",
            "/etc/grub.d/30_os-prober.dpkg-divert",
            "--rename",
            "/etc/grub.d/30_os-prober",
        ],
        cwd="/",
    )

    detect_virt_tool = "usr/bin/systemd-detect-virt"
    commands.run_with_nspawn(
        directory,
        ["dpkg-divert", "--local", "--rename", f"/{detect_virt_tool}"],
        cwd="/",
    )

    f = open(os.path.join(directory, detect_virt_tool), "w")
    f.write("exit 1\n")
    f.close()

    commands.run_with_nspawn(directory, ["chmod", "+x", detect_virt_tool])


def undivert_grub(directory: str) -> None:
    commands.run_with_nspawn(
        directory,
        [
            "dpkg-divert",
            "--remove",
            "--local",
            "--divert",
            "/etc/grub.d/30_os-prober.dpkg-divert",
            "--rename",
            "/etc/grub.d/30_os-prober",
        ],
        cwd="/",
    )

    detect_virt_tool = "usr/bin/systemd-detect-virt"
    os.remove(os.path.join(directory, detect_virt_tool))
    commands.run_with_nspawn(
        directory,
        ["dpkg-divert", "--remove", "--local", "--rename", f"/{detect_virt_tool}"],
        cwd="/",
    )


def install_grub(directory: str, device: str) -> None:
    """
    Install shim and grub and configure grub.
    This function will only work for amd64 and arm64.
    """
    packages = ["shim-signed"]

    # we only support legacy boot on x64
    if processor() == "x86_64":
        packages.append("grub-pc")

    install_extra_packages(device, directory, packages)

    efi_target = "x86_64-efi"
    if processor() == "aarch64":
        efi_target = "arm64-efi"

    bind_devices = glob(f"{device}*")

    commands.run_with_nspawn(
        directory,
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
        bind_devices=bind_devices,
    )

    if processor() == "x86_64":
        commands.run_with_nspawn(
            directory,
            ["grub-install", "--target=i386-pc", device],
            cwd="/",
            bind_devices=bind_devices,
        )

    divert_grub(directory)
    commands.run_with_nspawn(
        directory, ["update-grub"], bind_devices=bind_devices, cwd="/"
    )

    undivert_grub(directory)


def install_bootloader(bootloader: str, directory: str, device: str) -> None:
    if bootloader == "grub":
        install_grub(directory, device)
    else:
        raise ValueError(f"bootloader {bootloader} not supported")


def setup_source_list(mount_dir: str, mirror: str, series: str) -> None:
    f = open(os.path.join(mount_dir, "etc/apt/sources.list"), "w")
    components = "main universe multiverse restricted"
    f.write(f"deb {mirror} {series} {components}\n")
    f.write(f"deb {mirror} {series}-updates {components}\n")
    f.write(f"deb {mirror} {series}-security {components}\n")

    f.close()


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
        disk_utils.format_partition(
            disk.esp_map_device(), partition_format="vfat", label="UEFI"
        )

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
@click.option(
    "--mirror", type=str, default="http://archive.ubuntu.com/ubuntu", required=True
)
@click.option("--hostname", type=str, default="ubuntu", required=True)
@click.option("--apt-cache", type=str)
def debootstrap(
    output: str, series: str, mirror: str, hostname: str, apt_cache: str
) -> None:
    os.mkdir(output)
    if apt_cache is not None:
        mirror_without_scheme = mirror.split("://")[1]
        mirror = f"{apt_cache}/{mirror_without_scheme}"

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

    add_fstab_entry(mount_dir, "LABEL=rootfs\t/\text4\tdefaults\t0\t1")
    add_fstab_entry(mount_dir, "LABEL=UEFI\t/boot/efi\tvfat\tumask=0077\t0\t1")

    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)

    shutil.move(disk.path, disk_image)


@cli.command()
@click.option("--disk-image", type=str, default="disk.img", required=True)
@click.option(
    "--mirror", type=str, default="http://archive.ubuntu.com/ubuntu", required=True
)
@click.option("--series", type=str, required=True)
@click.option("--extra-package", multiple=True)
@click.option("--apt-cache", type=str)
def update_system(
    disk_image: str, mirror: str, series: str, extra_package: List[str], apt_cache: str
) -> None:
    disk = UEFIDisk.from_disk_image(disk_image)

    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)
    mount_partition(disk.esp_map_device(), f"{mount_dir}/boot/efi")

    if apt_cache is not None:
        setup_apt_cache(mount_dir, apt_cache)
    setup_source_list(mount_dir, mirror, series)
    saved_resolvconf = replace_resolv_conf(mount_dir, NAMESERVER)

    do_system_update(f"/dev/{disk.loop_device}", mount_dir)
    install_extra_packages(f"/dev/{disk.loop_device}", mount_dir, list(extra_package))

    remove_apt_cache(mount_dir)
    restore_resolv_conf(mount_dir, saved_resolvconf)
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

    file_map: Dict[str, str] = dict()
    for f in files:
        src, dst = f.split(":")
        file_map[dst] = src

    copy_extra_files(mount_dir, file_map)

    for dest in file_map:
        if owner is not None:
            commands.run_with_nspawn(mount_dir, ["chown", owner, dest])
        if mod is not None:
            commands.run_with_nspawn(mount_dir, ["chmod", mod, dest])

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

    for file_url in files:
        path, url = file_url.split(":")
        download_file(url, f"{mount_dir}/path")

    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)


@cli.command("install-grub")
@click.option("--disk-image", type=str, default="disk.img")
@click.option("--rootfs-label", type=str, default="rootfs")
@click.option("--apt-cache", type=str)
def install_grub_command(disk_image: str, rootfs_label: str, apt_cache: str) -> None:
    disk = UEFIDisk.from_disk_image(disk_image)

    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)
    mount_partition(disk.esp_map_device(), f"{mount_dir}/boot/efi")

    grub_conf_url = "https://gist.githubusercontent.com/gjolly/14ed79fa5323a1d7a7f653f8dda60921/raw/8df1830c1ce6aa80b23515d9420c9afdc987ee1d/extra-grub-config.cfg"  # noqa

    grub_config_dir = f"{mount_dir}/etc/default/grub.d"
    if not os.path.exists(grub_config_dir):
        os.mkdir(grub_config_dir)

    download_file(
        grub_conf_url, f"{mount_dir}/etc/default/grub.d/extra-grub-config.cfg"
    )

    if apt_cache is not None:
        setup_apt_cache(mount_dir, apt_cache)

    saved_resolvconf = replace_resolv_conf(mount_dir, NAMESERVER)
    install_bootloader("grub", mount_dir, f"/dev/{disk.loop_device}")

    commands.run(
        [
            "sed",
            "-i",
            "-e",
            f"s,root=[^ ]*,root=LABEL={rootfs_label},",
            f"{mount_dir}/boot/grub/grub.cfg",
        ]
    )

    remove_apt_cache(mount_dir)
    restore_resolv_conf(mount_dir, saved_resolvconf)
    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)


@cli.command()
@click.option("--disk-image", type=str, default="disk.img")
@click.option("--package", multiple=True)
@click.option("--apt-cache", type=str)
def install_packages(disk_image: str, package: List[str], apt_cache: str) -> None:
    disk = UEFIDisk.from_disk_image(disk_image)

    mount_dir = tempfile.mkdtemp(prefix="genesis-build")
    mount_partition(disk.rootfs_map_device(), mount_dir)
    mount_partition(disk.esp_map_device(), f"{mount_dir}/boot/efi")
    saved_resolvconf = replace_resolv_conf(mount_dir, NAMESERVER)

    if apt_cache is not None:
        setup_apt_cache(mount_dir, apt_cache)
    install_extra_packages(f"/dev/{disk.loop_device}", mount_dir, list(package))

    remove_apt_cache(mount_dir)
    restore_resolv_conf(mount_dir, saved_resolvconf)
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

    user_exists: bool = False
    with open(os.path.join(mount_dir, "etc/passwd")) as passwd:
        lines = passwd.readlines()
        users = [line.split(":")[0] for line in lines]
        user_exists = username in users

    if not user_exists:
        commands.run_with_nspawn(
            mount_dir,
            [
                "adduser",
                "--quiet",
                "--shell",
                "/bin/bash",
                "--gecos",
                "''",
                "--disabled-password",
                username,
            ],
        )

        # actually disable the password
        commands.run_with_nspawn(mount_dir, ["passwd", "--delete", username])

    if ssh_key is not None:
        # TODO: we should use path.join here
        home_dir = os.path.join(mount_dir, f"home/{username}")
        commands.run(["mkdir", "-p", f"{home_dir}/.ssh"])

        ssh_key_file = os.path.join(home_dir, ".ssh/authorized_keys")
        with open(ssh_key_file, "w") as key_file:
            key_file.write(ssh_key)

    if sudo:
        commands.run_with_nspawn(mount_dir, ["usermod", "-aG", "sudo", username])

    umount_all(mount_dir)
    teardown_loop_device(disk.loop_device)
    os.rmdir(mount_dir)


if __name__ == "__main__":
    cli()
