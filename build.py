import os
import subprocess
import tempfile

import click
import yaml

DEFAULT_MIRROR='http://archive.ubuntu.com/ubuntu/'
SYSTEM_ROOT = os.open("/", os.O_RDONLY)


class Config():
    series: str
    mirror: str
    kernel_package: str
    extra_packages: list[str]
    build_ppas: list[str]
    image_size: int

    def __init__(self, config_path) -> None:
        with open(config_path) as config_file:
            config = yaml.safe_load(config_file)

        self.series = config['series']
        self.mirror = config.get('mirror', DEFAULT_MIRROR)
        self.extra_packages = config.get('extra_packages', list())
        self.build_ppas = config.get('build_ppas', list())
        self.kernel_package = config.get('kernel_package', 'linux-virtual')
        self.image_size = config.get('image_size', 3)


def run_command(cmd: list[str]) -> None:
    proc = subprocess.Popen(cmd, shell=False)

    proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f'{cmd} failed')


def run_command_and_save_output(cmd: list[str]) -> str:
    result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    if result.returncode != 0:
        raise RuntimeError(f'{cmd} failed')

    return result.stdout.decode()


def run_deboostrap(conf: Config, build_dir_path: str) -> None:
    run_command(
            ['/usr/sbin/debootstrap',
                conf.series,
                build_dir_path,
                conf.mirror])


def add_build_ppas(conf: Config):
    pass


def install_extra_packages(conf: Config):
    run_command(['/usr/bin/apt-get', 'install', '-y'] + conf.extra_packages)


def do_system_update():
    run_command(['/usr/bin/apt-get', 'update'])
    run_command(['/usr/bin/apt-get', '-y', 'upgrade'])


def exit_chroot():
    os.fchdir(SYSTEM_ROOT)
    os.chroot(".")


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


def format_ext4_partition(device: str, label: str = 'rootfs') -> None:
    if label == '':
        # TODO: allow no label to be passed
        raise ValueError('no label passed')
    label_flag = f'-L {label}'

    run_command([
        'mkfs.ext4', '-F',
        '-b', '4096',
        '-i', '8192',
        '-m', '0',
        label_flag,
        '-E', 'resize=536870912',
        device
        ])


def format_partition(device: str, partition_format='ext4') -> None:
    if partition_format == 'ext4':
        format_ext4_partition(device)
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


@click.command()
@click.option('--config', '-c', type=str, required=True)
def main(config: str) -> None:
    conf = Config(config)

    #build_dir = tempfile.TemporaryDirectory()
    #run_deboostrap(conf, build_dir.name)

    #real_root = os.open("/", os.O_RDONLY)
    #os.chroot(build_dir.name)

    #add_build_ppas(conf)

    #do_system_update()
    #install_extra_packages(conf)

    #exit_chroot()

    disk_image = create_empty_disk(conf.image_size)
    partition_disk(disk_image)
    loop_device = setup_loop_device(disk_image)

    rootfs_part_device = f'/dev/mapper/{loop_device}p1'
    esp_part_device = f'/dev/mapper/{loop_device}p15'

    format_partition(rootfs_part_device, partition_format='ext4')

    mount_dir = tempfile.TemporaryDirectory()
    mount_partition(rootfs_part_device, mount_dir.name)

    print(rootfs_part_device, esp_part_device, mount_dir.name)

    os.close(SYSTEM_ROOT)
    #mount_dir.cleanup()
    #build_dir.cleanup()


if __name__ == '__main__':
    main()
