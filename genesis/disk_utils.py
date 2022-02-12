import tempfile

import genesis.commands as commands


def create_empty_disk(size: int) -> str:
    """
    Create an empty disk image
    :param size: size of the disk (in GigaBytes)
    :return: location of the disk
    """
    disk_path = tempfile.mktemp(prefix='genesis', suffix='.img')
    commands.run([
        '/usr/bin/qemu-img',
        'create',
        disk_path, str(size)+'G'])

    return disk_path


def partition_uefi_disk(disk_image_path: str) -> None:
    """
    Partition the disk image. TODO: return the partition numbers
    """
    commands.run([
        '/usr/sbin/sgdisk',
        disk_image_path,
        '--zip-all'])

    commands.run([
        '/usr/sbin/sgdisk',
        disk_image_path,
        '--new=14::+4M',
        '--new=15::+106M',
        '--new=1::'
        ])

    commands.run([
        '/usr/sbin/sgdisk',
        disk_image_path,
        '-t', '14:ef02',
        '-t', '15:ef00'
        ])

    commands.run([
        '/usr/sbin/sgdisk',
        disk_image_path,
        '--print'
        ])


def format_ext4_partition(device: str, label: str) -> None:
    if label == '':
        # TODO: allow no label to be passed
        raise ValueError('no label passed')

    commands.run([
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

    commands.run([
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
