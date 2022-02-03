import os
import subprocess
import tempfile

import click
import yaml
import shutil

DEFAULT_MIRROR='http://archive.ubuntu.com/ubuntu/'
SYSTEM_ROOT = os.open("/", os.O_RDONLY)
CWD = os.getcwd()


class Config():
    series: str
    mirror: str
    kernel_package: str
    extra_packages: list[str]
    build_ppas: list[str]
    image_size: int
    system_mirror: str
    bootloader: str
    files: dict[str, str]

    def __init__(self, config_path) -> None:
        with open(config_path) as config_file:
            config = yaml.safe_load(config_file)

        self.series = config['series']
        self.mirror = config.get('mirror', DEFAULT_MIRROR)
        self.extra_packages = config.get('extra_packages', list())
        self.build_ppas = config.get('build_ppas', list())
        self.kernel_package = config.get('kernel_package', 'linux-virtual')
        self.image_size = config.get('image_size', 3)
        self.system_mirror = config.get('system_mirror', DEFAULT_MIRROR)
        self.bootloader = config.get('bootloader', 'grub')
        self.files = config.get('files', dict())


def run_command(cmd: list[str]) -> None:
    shell_form_cmd = ' '.join(cmd)
    print(f'>> {shell_form_cmd}')

    proc = subprocess.Popen(cmd, shell=False)

    proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f'{cmd} failed')


def run_command_and_save_output(cmd: list[str]) -> str:
    shell_form_cmd = ' '.join(cmd)
    print(f'>> {shell_form_cmd}')

    result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    if result.returncode != 0:
        raise RuntimeError(f'{cmd} failed')

    return result.stdout.decode()


def deboostrap(conf: Config, build_dir_path: str) -> None:
    run_command(
            ['/usr/sbin/debootstrap',
                conf.series,
                build_dir_path,
                conf.mirror])


def add_build_ppas(ppas: list[str]):
    pass


def install_extra_packages(packages: list[str]):
    run_command(['/usr/bin/apt-get', 'install', '-y'] + packages)


def do_system_update():
    run_command(['/usr/bin/apt-get', 'update'])
    run_command(['/usr/bin/apt-get', '-y', 'upgrade'])


def exit_chroot():
    os.fchdir(SYSTEM_ROOT)
    os.chroot(".")

    os.chdir(CWD)


def create_empty_disk(size: int) -> str:
    """
    Create an empty disk image
    :param size: size of the disk (in GigaBytes)
    :return: location of the disk
    """
    disk_path = 'disk.img'
    run_command([
        '/usr/bin/qemu-img',
        'create',
        disk_path, str(size)+'G'])

    return disk_path


def partition_disk(disk_image_path: str) -> None:
    """
    Partition the disk image. TODO: return the partition numbers
    """
    run_command([
        '/usr/sbin/sgdisk',
        disk_image_path,
        '--zip-all'])

    run_command([
        '/usr/sbin/sgdisk',
        disk_image_path,
        '--new=14::+4M',
        '--new=15::+106M',
        '--new=1::'
        ])

    run_command([
        '/usr/sbin/sgdisk',
        disk_image_path,
        '-t', '14:ef02',
        '-t', '15:ef00'
        ])

    run_command([
        '/usr/sbin/sgdisk',
        disk_image_path,
        '--print'
        ])


def format_ext4_partition(device: str, label: str) -> None:
    if label == '':
        # TODO: allow no label to be passed
        raise ValueError('no label passed')

    run_command([
        'mkfs.ext4', '-F',
        '-b', '4096',
        '-i', '8192',
        '-m', '0',
        '-L', label,
        '-E', 'resize=536870912',
        device
        ])


def format_vfat_partition(device: str, label: str) -> None:
    if label == '':
        # TODO: allow no label to be passed
        raise ValueError('no label passed')

    run_command([
        'mkfs.vfat',
        '-F', '32',
        '-n', label,
        device
        ])


def format_partition(
        device: str,
        partition_format: str = 'ext4', label: str = 'rootfs') -> None:
    if partition_format == 'ext4':
        format_ext4_partition(device, label)
    elif partition_format == 'vfat':
        format_vfat_partition(device, label)
    else:
        raise ValueError(f'partition type {partition_format} unsupported')


def setup_loop_device(disk_image_path: str) -> str:
    run_command(['kpartx', '-s', '-v', '-a', disk_image_path])

    out = run_command_and_save_output([
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
    run_command(['mount', rootfs_partition, mount_dir])


def add_fstab_entry(entry: str):
    f = open('/etc/fstab', 'w')
    f.write(f'{entry}\n')
    f.close()


def umount_all(mount_dir: str):
    run_command(['umount', '-R', mount_dir])


def teardown_loop_device(device: str):
    run_command(['losetup', '-d', f'/dev/{device}'])


def divert_grub() -> None:
    run_command([
        'dpkg-divert', '--local',
        '--divert', '/etc/grub.d/30_os-prober.dpkg-divert',
        '--rename', '/etc/grub.d/30_os-prober'
        ])

    detect_virt_tool = '/usr/bin/systemd-detect-virt'
    run_command([
        'dpkg-divert', '--local',
        '--rename', detect_virt_tool
        ])

    f = open(detect_virt_tool, 'w')
    f.write('exit 1\n')
    f.close()

    run_command(['chmod', '+x', detect_virt_tool])

def undivert_grub() -> None:
    run_command([
        'dpkg-divert', '--remove', '--local',
        '--divert', '/etc/grub.d/30_os-prober.dpkg-divert',
        '--rename', '/etc/grub.d/30_os-prober'
        ])

    detect_virt_tool = '/usr/bin/systemd-detect-virt'
    os.remove(detect_virt_tool)
    run_command([
        'dpkg-divert', '--remove', '--local',
        '--rename', detect_virt_tool
        ])


def install_grub(device: str) -> None:
    install_extra_packages(['shim-signed', 'grub-pc'])

    run_command([
        'grub-install',
        device,
        '--boot-directory=/boot',
        '--efi-directory=/boot/efi',
        '--target=x86_64-efi',
        '--uefi-secure-boot',
        '--no-nvram'
        ])

    run_command([
        'grub-install',
        '--target=i386-pc',
        device
        ])

    divert_grub()
    run_command(['update-grub'])
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
    run_command(['mount', 'dev-live', '-t', 'devtmpfs', f'{mount_dir}/dev'])
    run_command(['mount', 'proc-live', '-t', 'proc', f'{mount_dir}/proc'])
    run_command(['mount', 'sysfs-live', '-t', 'sysfs', f'{mount_dir}/sys'])
    run_command(['mount', 'securityfs', '-t', 'securityfs', f'{mount_dir}/sys/kernel/security'])
    run_command(['mount', '-t', 'cgroup2', 'none', f'{mount_dir}/sys/fs/cgroup'])
    run_command(['mount', '-t', 'tmpfs', 'none', f'{mount_dir}/tmp'])
    run_command(['mount', '-t', 'tmpfs', 'none', f'{mount_dir}/var/lib/apt'])
    run_command(['mount', '-t', 'tmpfs', 'none', f'{mount_dir}/var/cache/apt'])


def copy_extra_files(mount_dir: str, files: dict[str, str]) -> None:
    for dest, local in files.items():
        print(f'COPYING {local} -> {dest}')
        shutil.copy(local, f'{mount_dir}{dest}')


@click.command()
@click.option('--config', '-c', type=str, required=True)
def main(config: str) -> None:
    conf = Config(config)

    disk_image = create_empty_disk(conf.image_size)
    partition_disk(disk_image)
    loop_device = setup_loop_device(disk_image)

    rootfs_part_device = f'/dev/mapper/{loop_device}p1'
    esp_part_device = f'/dev/mapper/{loop_device}p15'

    format_partition(rootfs_part_device, partition_format='ext4')
    format_partition(esp_part_device, partition_format='vfat', label='UEFI')

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
    install_extra_packages(conf.extra_packages)

    copy_extra_files(mount_dir, conf.files)

    install_bootloader(conf.bootloader, f'/dev/{loop_device}')

    exit_chroot()

    umount_all(mount_dir)
    os.rmdir(mount_dir)
    teardown_loop_device(loop_device)

    os.close(SYSTEM_ROOT)

if __name__ == '__main__':
    main()
