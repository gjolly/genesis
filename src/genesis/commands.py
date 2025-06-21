import subprocess

from typing import List


def run_with_nspawn(
    directory: str,
    cmd: List[str],
    environment: dict[str, str] = dict(),
    cwd: str = "",
    bind_devices: List[str] = list(),
) -> None:
    """
    Run a command with systemd-nspawn.
    """
    shell_form_cmd = " ".join(cmd)
    print(f">> {shell_form_cmd}")

    env_params = []
    for key, value in environment.items():
        env_params += ["--setenv", f"{key}={value}"]

    binds_params = []
    for device in bind_devices:
        binds_params += ["--bind", device]

    process_cwd = None
    if cwd != "":
        process_cwd = cwd
    proc = subprocess.Popen(
        ["systemd-nspawn", "--quiet", f"--directory={directory}"]
        + env_params
        + binds_params
        + cmd,
        cwd=process_cwd,
        shell=False,
    )

    proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"{cmd} failed")


def run(cmd: List[str], cwd: str = "") -> None:
    shell_form_cmd = " ".join(cmd)
    print(f">> {shell_form_cmd}")

    process_cwd = None
    if cwd != "":
        process_cwd = cwd
    proc = subprocess.Popen(cmd, cwd=process_cwd, shell=False)

    proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"{cmd} failed")


def run_and_save_output(cmd: List[str]) -> str:
    shell_form_cmd = " ".join(cmd)
    print(f">> {shell_form_cmd}")

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    if result.returncode != 0:
        raise RuntimeError(f"{cmd} failed")

    return result.stdout.decode()
