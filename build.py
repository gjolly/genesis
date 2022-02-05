import os
import subprocess
import tempfile
import click
import yaml
import shutil

from typing import Any

import snaps
import commands
import config
import disk_utils

SYSTEM_ROOT = os.open("/", os.O_RDONLY)
CWD = os.getcwd()

def deboostrap(conf: config.Config, build_dir_path: str) -> None:
    commands.run(['/usr/sbin/debootstrap',
                  conf.series,
                  build_dir_path,
                  conf.mirror])


def add_build_ppas(ppas: list[str]):
    pass


def install_extra_packages(packages: list[str]):
    commands.run(['/usr/bin/apt-get', 'install', '-y'] + packages)


def do_system_update():
    commands.run(['/usr/bin/apt-get', 'update'])
    commands.run(['/usr/bin/apt-get', '-y', 'upgrade'])


def exit_chroot():
    os.fchdir(SYSTEM_ROOT)
    os.chroot(".")

    os.chdir(CWD)


def setup_loop_device(disk_image_path: str) -> str:
    commands.run(['kpartx', '-s', '-v', '-a', disk_image_path])

    out = commands.run_and_save_output([
        'losetup', '--noheadings', '--output', 'NAME,BACK-FILE', '-l'])
    lines_out = out.rstrip().split('\n')

    for line in lines_out:
        fields = line.split()
        device, file_path = fields[0], fields[1]
        if disk_image_path in file_path:
            loop_device = device.removeprefix('/dev/')
            break

    return loop_device


def mount_partition(
        rootfs_partition: str, mount_dir: str) -> None:
    commands.run(['mount', rootfs_partition, mount_dir])


def add_fstab_entry(entry: str):
    f = open('/etc/fstab', 'a')
    f.write(f'{entry}\n')
    f.close()


def umount_all(mount_dir: str):
    commands.run(['umount', '-R', mount_dir])


def teardown_loop_device(device: str):
    out = commands.run_and_save_output(['dmsetup', 'table'])
    for line in out.rstrip().split('\n'):
        mapper_dev = line.split(':')[0]
        if device in mapper_dev:
            commands.run(['dmsetup', 'remove', f'/dev/mapper/{mapper_dev}'])

    commands.run(['losetup', '-d', f'/dev/{device}'])


def divert_grub() -> None:
    commands.run([
        'dpkg-divert', '--local',
        '--divert', '/etc/grub.d/30_os-prober.dpkg-divert',
        '--rename', '/etc/grub.d/30_os-prober'
        ])

    detect_virt_tool = '/usr/bin/systemd-detect-virt'
    commands.run([
        'dpkg-divert', '--local',
        '--rename', detect_virt_tool
        ])

    f = open(detect_virt_tool, 'w')
    f.write('exit 1\n')
    f.close()

    commands.run(['chmod', '+x', detect_virt_tool])

def undivert_grub() -> None:
    commands.run([
        'dpkg-divert', '--remove', '--local',
        '--divert', '/etc/grub.d/30_os-prober.dpkg-divert',
        '--rename', '/etc/grub.d/30_os-prober'
        ])

    detect_virt_tool = '/usr/bin/systemd-detect-virt'
    os.remove(detect_virt_tool)
    commands.run([
        'dpkg-divert', '--remove', '--local',
        '--rename', detect_virt_tool
        ])


def install_grub(device: str) -> None:
    install_extra_packages(['shim-signed', 'grub-pc'])

    commands.run([
        'grub-install',
        device,
        '--boot-directory=/boot',
        '--efi-directory=/boot/efi',
        '--target=x86_64-efi',
        '--uefi-secure-boot',
        '--no-nvram'
        ])

    commands.run([
        'grub-install',
        '--target=i386-pc',
        device
        ])

    divert_grub()
    commands.run(['update-grub'])
    undivert_grub()


def install_bootloader(bootloader: str, device: str) -> None:
    if bootloader == 'grub':
        install_grub(device)
    else:
        raise ValueError(f'bootloader {bootloader} not supported')


def setup_source_list(mirror: str, series: str) -> None:
    f = open('/etc/apt/sources.list', 'w')
    f.write(f'deb {mirror} {series} main\n')
    f.write(f'deb {mirror} {series}-updates main\n')
    f.write(f'deb {mirror} {series}-security main\n')

    f.close()


def mount_virtual_filesystems(mount_dir):
    commands.run(['mount', 'dev-live', '-t', 'devtmpfs', f'{mount_dir}/dev'])
    commands.run(['mount', 'proc-live', '-t', 'proc', f'{mount_dir}/proc'])
    commands.run(['mount', 'sysfs-live', '-t', 'sysfs', f'{mount_dir}/sys'])
    commands.run(['mount', 'securityfs', '-t', 'securityfs', f'{mount_dir}/sys/kernel/security'])
    commands.run(['mount', '-t', 'cgroup2', 'none', f'{mount_dir}/sys/fs/cgroup'])
    commands.run(['mount', '-t', 'tmpfs', 'none', f'{mount_dir}/tmp'])
    commands.run(['mount', '-t', 'tmpfs', 'none', f'{mount_dir}/var/lib/apt'])
    commands.run(['mount', '-t', 'tmpfs', 'none', f'{mount_dir}/var/cache/apt'])


def copy_extra_files(mount_dir: str, files: dict[str, str]) -> None:
    for dest, local in files.items():
        print(f'COPYING {local} -> {dest}')

        shutil.copy(local, f'{mount_dir}{dest}')


def convert_binary_image(
        disk_image: str, binary_format: str, out_path: str) -> None:
    commands.run([
        'qemu-img',
        'convert', '-f', 'raw', '-O', binary_format,
        disk_image,
        out_path,
        ])


@click.command()
@click.option('--config-file', '-c', type=str, required=True)
def main(config_file: str) -> None:
    conf = config.Config(config_file)

    disk_image = disk_utils.create_empty_disk(conf.image_size)
    disk_utils.partition_disk(disk_image)
    loop_device = setup_loop_device(disk_image)

    rootfs_part_device = f'/dev/mapper/{loop_device}p1'
    esp_part_device = f'/dev/mapper/{loop_device}p15'

    disk_utils.format_partition(rootfs_part_device, partition_format='ext4')
    disk_utils.format_partition(
            esp_part_device, partition_format='vfat', label='UEFI')

    mount_dir = tempfile.mkdtemp(prefix='genesis')
    mount_partition(rootfs_part_device, mount_dir)

    deboostrap(conf, mount_dir)

    os.mkdir(f'{mount_dir}/boot/efi')
    mount_partition(esp_part_device, f'{mount_dir}/boot/efi')

    mount_virtual_filesystems(mount_dir)

    os.chroot(mount_dir)
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'

    add_fstab_entry('LABEL=rootfs\t/\text4\tdefaults\t0\t1')
    add_fstab_entry('LABEL=UEFI\t/boot/efi\tvfat\tumask=0077\t0\t1')

    add_build_ppas(conf.build_ppas)

    setup_source_list(conf.mirror, conf.series)

    do_system_update()

    # make sure we install snapd if snap preseeding is neededj
    if len(conf.snaps) > 1 and 'snapd' not in conf.extra_packages:
        conf.extra_packages.append('snapd')

    install_extra_packages(conf.extra_packages)

    exit_chroot()

    copy_extra_files(mount_dir, conf.files)

    os.chroot(mount_dir)

    install_bootloader(conf.bootloader, f'/dev/{loop_device}')

    exit_chroot()

    snaps.preseed(conf.snaps, mount_dir)

    umount_all(mount_dir)
    os.rmdir(mount_dir)
    teardown_loop_device(loop_device)

    if conf.binary_format != 'raw':
        convert_binary_image(disk_image, conf.binary_format, conf.out_path)
    else:
        shutil.move(disk_image, conf.out_path)

    os.remove(disk_image)
    os.close(SYSTEM_ROOT)

if __name__ == '__main__':
    main()
