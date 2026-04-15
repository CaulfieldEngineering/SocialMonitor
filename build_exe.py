"""Build script to produce SocialMonitor.exe via PyInstaller."""

import subprocess
import sys


def main():
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=SocialMonitor",
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        # Source plugins
        "--hidden-import=social_monitor.sources.reddit",
        "--hidden-import=social_monitor.sources.kvr_audio",
        "--hidden-import=social_monitor.sources.stackoverflow",
        "--hidden-import=social_monitor.sources.gearspace",
        "--hidden-import=social_monitor.sources.discord_bot",
        "--hidden-import=social_monitor.sources.rss_feed",
        # UI modules
        "--hidden-import=social_monitor.ui.tray",
        "--hidden-import=social_monitor.ui.main_window",
        "--hidden-import=social_monitor.ui.signals",
        "--hidden-import=social_monitor.ui.settings_dialog",
        "--hidden-import=social_monitor.ui.log_viewer",
        "--hidden-import=social_monitor.ui.widgets",
        # Exclude conflicts and bloat
        "--exclude-module=PyQt5",
        "--exclude-module=PySide2",
        "--exclude-module=PySide6",
        "--exclude-module=tkinter",
        "--exclude-module=torch",
        "--exclude-module=torchvision",
        "--exclude-module=tensorflow",
        "--exclude-module=scipy",
        "--exclude-module=numpy",
        "--exclude-module=pandas",
        "--exclude-module=matplotlib",
        "--exclude-module=h5py",
        "--exclude-module=onnxruntime",
        "--exclude-module=playwright",
        # Entry point
        "src/social_monitor/__main__.py",
    ]

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print("\nBuild complete! Output in dist/SocialMonitor/")
    print("Run dist/SocialMonitor/SocialMonitor.exe to launch.")


if __name__ == "__main__":
    main()
