import os
import pathlib
import yaml
import tempfile
import shutil

from typing import Any, Union

import genesis.commands as commands

def get_info(snap_path: str) -> dict[str, Any]:
    snap_info_raw = commands.run_and_save_output([
        'snap', 'info', '--verbose', snap_path
        ])

    snap_info = yaml.safe_load(snap_info_raw)

    return snap_info


def preseeded(snap: str, snap_dir: str) -> bool:
    files = os.listdir(snap_dir)
    for f in files:
        if snap in f:
            return True

    return False


def create_directory(directory: str) -> None:
    pathlib.Path(
        directory
    ).mkdir(parents=True, exist_ok=True)


def delete_signature(content: str) -> str:
    """
    Remove the signature lines from a "snap configuration" file
    eg.
    foo: bar
    test: tost
    lol: lala

    xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    The last part will be removed
    """

    split_content = content.split('\n\n')

    return split_content[0]


def prepare_assertions(assertion_dir: str,
        brand: str = 'generic',
        model: str = 'generic-classic'):
    model_path = f'{assertion_dir}/model'
    account_key_path = f'{assertion_dir}/account-key'
    account_path = f'{assertion_dir}/account'

    out = commands.run_and_save_output([
        'snap', 'known', '--remote', 'model',
        'series=16', f'model={model}', f'brand-id={brand}'])

    with open(model_path, 'w') as model_file:
        model_file.write(out)

    content = delete_signature(out)

    model_obj = yaml.safe_load(content)
    key = model_obj['sign-key-sha3-384']

    out = commands.run_and_save_output([
        'snap', 'known', '--remote', 'account-key',
        f'public-key-sha3-384={key}'])

    with open(account_key_path, 'w') as account_key_file:
        account_key_file.write(out)

    #snap known --remote account account-id=$account
    content = delete_signature(out)
    account_obj = yaml.safe_load(content)
    account_id = account_obj['account-id']

    out = commands.run_and_save_output([
        'snap', 'known', '--remote', 'account',
        f'account-id={account_id}'])

    with open(account_path, 'w') as account_file:
        account_file.write(out)


def preseed_snap(
        snap: str, channel: str, classic: bool,
        snap_installed: dict[str, list[dict[str, Union[str, bool]]]],
        mount_dir: str) -> None:
    """
    Pre-install a snap on the system. Also check what its snap base is
    and pre-install it if needed.
    :param snap: the name of the snap to install
    :param channel: the channel where to install the snap from
    :param snap_installed: a dictinary containing the installed snap that
                           will be updated once the snap will be installed
    """
    cwd = os.getcwd()
    workdir = tempfile.TemporaryDirectory()
    os.chdir(workdir.name)

    seed_dir = f'{mount_dir}/var/lib/snapd/seed'
    assertion_dir = f'{seed_dir}/assertions'
    snap_dir = f'{seed_dir}/snaps'

    if preseeded(snap, snap_dir):
        return

    os.environ['UBUNTU_STORE_ARCH'] = 'amd64'
    os.environ['SNAPPY_STORE_NO_CDN'] = '1'

    commands.run([
        'snap', 'download', f'--channel={channel}', snap
        ])

    files = os.listdir(workdir.name)
    for f in files:
        if f.endswith('.snap'):
            snap_file = f
            shutil.move(f, snap_dir)
            info = get_info(f)
            if 'base' in info:
                preseed_snap(
                        info['base'],
                        'stable', False, snap_installed, mount_dir)
        elif f.endswith('.assert'):
            shutil.move(f, assertion_dir)

    snap_installed['snaps'].append({
        'name': snap,
        'channel': channel,
        'file': snap_file,
        'classic': classic,
    })

    os.chdir(cwd)


def preseed(snaps: dict[str, dict[str, str]], mount_dir: str):
    """
    Pre-install snaps on the image
    """
    if len(snaps) == 0:
        return

    seed_dir = f'{mount_dir}/var/lib/snapd/seed'
    assertion_dir = f'{seed_dir}/assertions'
    snap_dir = f'{seed_dir}/snaps'

    create_directory(seed_dir)
    create_directory(assertion_dir)
    create_directory(snap_dir)

    prepare_assertions(assertion_dir)

    seed_yaml = f'{mount_dir}/var/lib/snapd/seed/seed.yaml'
    snaps_installed: dict[str, list[dict[str, Union[str, bool]]]] = {
            'snaps': []}

    # just install snapd, we could avoid that if there was
    # no snap with bases >= core18 but we are lazy
    if 'snapd' not in snaps:
        snaps['snapd'] = {
            'channel': 'stable',
            'classic': 'false'
        }

    for snap, spec in snaps.items():
        classic = True if 'classic' in spec and spec['classic'] else False
        preseed_snap(
                snap, spec['channel'], classic, snaps_installed, mount_dir)

    snap_seed_yaml = yaml.dump(snaps_installed)
    with open(seed_yaml, 'w') as seed:
        seed.write(snap_seed_yaml)

    # validate the seed file we just written
    commands.run(['snap', 'debug', 'validate-seed', seed_yaml])
