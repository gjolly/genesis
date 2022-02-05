import os
import subprocess
import shutil

def run(cmd: list[str]) -> None:
    shell_form_cmd = ' '.join(cmd)
    print(f'>> {shell_form_cmd}')

    proc = subprocess.Popen(cmd, shell=False)

    proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f'{cmd} failed')


def run_and_save_output(cmd: list[str]) -> str:
    shell_form_cmd = ' '.join(cmd)
    print(f'>> {shell_form_cmd}')

    result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    if result.returncode != 0:
        raise RuntimeError(f'{cmd} failed')

    return result.stdout.decode()
