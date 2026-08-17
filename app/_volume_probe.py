"""Throwaway probe: does volume= on a Flash Endpoint actually mount, and where?
Not part of the app -- delete after use."""

from runpod_flash import CpuInstanceType, Endpoint, NetworkVolume

probe = Endpoint(
    name="mflux-volume-probe",
    cpu=CpuInstanceType.CPU3C_1_2,
    workers=(0, 1),
    idle_timeout=60,
    volume=NetworkVolume(name="mflux-probe-vol", size=10),
)


@probe
async def check(**_kwargs) -> dict:
    import os
    import subprocess

    root_listing = os.listdir("/")
    mount_candidates = [
        p for p in ("/workspace", "/runpod-volume", "/volume", "/data", "/mnt")
        if os.path.isdir(p)
    ]
    write_results = {}
    for path in mount_candidates:
        test_file = os.path.join(path, "probe_test.txt")
        try:
            with open(test_file, "w") as f:
                f.write("probe")
            with open(test_file) as f:
                content = f.read()
            write_results[path] = {"write_ok": True, "read_back": content}
        except Exception as exc:  # noqa: BLE001
            write_results[path] = {"write_ok": False, "error": str(exc)}

    df_output = subprocess.run(["df", "-h"], capture_output=True, text=True, timeout=10).stdout

    return {
        "root_listing": root_listing,
        "mount_candidates": mount_candidates,
        "write_results": write_results,
        "df": df_output,
    }
