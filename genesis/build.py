import os
import tempfile
import click
import shutil

import genesis.snaps as snaps
import genesis.commands as commands
import genesis.config as config
import genesis.disk_utils as disk_utils


SYSTEM_ROOT = os.open("/", os.O_RDONLY)
CWD = os.getcwd()


class BuildState():
    directories_to_umount: list[str]
    files_to_delete: list[str]
    loop_to_detach: list[str]
    in_chroot: bool
    disk_image: str
    success: bool

    def __init__(self):
        self.in_chroot = False
        self.directories_to_umount = list()
        self.files_to_delete = list()
        self.loop_to_detach = list()
        self.disk_image = list()
        self.success = False


def deboostrap(conf: config.Config, build_dir_path: str) -> None:
    commands.run(['/usr/sbin/debootstrap',
                  conf.series,
                  build_dir_path,
                  conf.mirror])


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
    commands.run([
        'mount', 'securityfs', '-t', 'securityfs', f'{mount_dir}/sys/kernel/security'])
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


def cleanup(build_state: BuildState, debug: bool):
    if build_state.in_chroot:
        exit_chroot()

    for mount in build_state.directories_to_umount:
        umount_all(mount)

    for device in build_state.loop_to_detach:
        teardown_loop_device(device)

    for f in build_state.files_to_delete:
        if os.path.isdir(f):
            os.rmdir(f)
        else:
            os.remove(f)

    if not build_state.success and not debug:
        os.remove(build_state.disk_image)
    elif not build_state.success:
        print(f'disk image kept for inspection: {build_state.disk_image}')


@click.command()
@click.option('--config-file', '-c', type=str, required=True)
@click.option('--debug', is_flag=True, default=False)
@click.option('--disk', '-d', type=str, required=False, default=None)
def main(config_file: str, debug: bool, disk: str) -> None:
    conf = config.Config(config_file)
    build_state = BuildState()

    # if the user provides the disk, let's not remove it at the end
    if disk is not None:
        debug = True

    try:
        build(conf, build_state, disk_path=disk)
    finally:
        cleanup(build_state, debug)


class UEFIDisk:
    path: str
    loop_device: str
    esp_partition_number: int
    rootfs_partition_number: int

    def __init__(self, size: int, path: str = None) -> None:
        """
        Create an empty disk image file with the right partition layout.
        If a disk path is supplied, only attach loop devices (we assume the disk
        has already been setup).
        """
        self.rootfs_partition_number = 1
        self.esp_partition_number = 15

        if path is not None:
            self.path = path
            self.loop_device = setup_loop_device(self.path)
            return

        self.path = disk_utils.create_empty_disk(size)
        disk_utils.partition_uefi_disk(self.path)
        self.loop_device = setup_loop_device(self.path)

        disk_utils.format_partition(
            self.rootfs_map_device(), partition_format='ext4')
        disk_utils.format_partition(
                self.esp_map_device(), partition_format='vfat', label='UEFI')

    def rootfs_map_device(self) -> str:
        return f'/dev/mapper/{self.loop_device}p{self.rootfs_partition_number}'

    def esp_map_device(self) -> str:
        return f'/dev/mapper/{self.loop_device}p{self.esp_partition_number}'


def build(conf: config.Config, state: BuildState, disk_path: str = None):
    """
    High level build steps
    """
    disk = UEFIDisk(conf.image_size, disk_path)
    state.disk_image = disk.path
    state.loop_to_detach.append(disk.loop_device)

    mount_dir = tempfile.mkdtemp(prefix='genesis')
    mount_partition(disk.rootfs_map_device(), mount_dir)
    state.directories_to_umount.append(mount_dir)
    state.files_to_delete.append(mount_dir)

    deboostrap(conf, mount_dir)

    os.mkdir(f'{mount_dir}/boot/efi')
    mount_partition(disk.esp_map_device(), f'{mount_dir}/boot/efi')

    mount_virtual_filesystems(mount_dir)

    os.chroot(mount_dir)
    state.in_chroot = True

    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'

    add_fstab_entry('LABEL=rootfs\t/\text4\tdefaults\t0\t1')
    add_fstab_entry('LABEL=UEFI\t/boot/efi\tvfat\tumask=0077\t0\t1')

    setup_source_list(conf.mirror, conf.series)

    do_system_update()

    # make sure we install snapd if snap preseeding is neededj
    if len(conf.snaps) > 0 and 'snapd' not in conf.extra_packages:
        conf.extra_packages.append('snapd')

    install_extra_packages(conf.extra_packages)

    exit_chroot()
    state.in_chroot = False

    copy_extra_files(mount_dir, conf.files)

    os.chroot(mount_dir)
    state.in_chroot = True

    install_bootloader(conf.bootloader, f'/dev/{disk.loop_device}')

    exit_chroot()
    state.in_chroot = False

    snaps.preseed(conf.snaps, mount_dir)

    if conf.binary_format != 'raw':
        convert_binary_image(disk.path, conf.binary_format, conf.out_path)
        os.remove(disk.path)
    else:
        shutil.move(disk.path, conf.out_path)

    state.success = True

    os.close(SYSTEM_ROOT)


if __name__ == '__main__':
    main()
