import yaml

DEFAULT_MIRROR='http://archive.ubuntu.com/ubuntu/'

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
    binary_format: str
    out_path: str
    snaps: dict[str, str]

    def __init__(self, config_path) -> None:
        with open(config_path) as config_file:
            config = yaml.safe_load(config_file)

        # TODO: validate the YAML file. Because python
        # is not strongly typed, we might be doing things
        # wrong if the user has a broken config file.
        self.series = config['series']
        self.mirror = config.get('mirror', DEFAULT_MIRROR)
        self.extra_packages = config.get('extra_packages', list())
        self.build_ppas = config.get('build_ppas', list())
        self.kernel_package = config.get('kernel_package', 'linux-virtual')
        self.image_size = config.get('image_size', 3)
        self.system_mirror = config.get('system_mirror', DEFAULT_MIRROR)
        self.bootloader = config.get('bootloader', 'grub')
        self.files = config.get('files', dict())
        self.binary_format = config.get('binary_format', 'raw')
        self.out_path = config.get('out_path', './ubuntu.img')
        self.snaps = config.get('snaps', dict())
