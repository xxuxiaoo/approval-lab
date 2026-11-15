"""Desktop shortcuts need to work without a terminal's Python/Conda PATH additions."""

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows startup scripts")


@pytest.mark.parametrize("script", ["start.bat", "start.ps1"])
def test_shortcut_uses_project_python_without_a_working_global_python(tmp_path, script):
    workspace = Path(__file__).resolve().parents[1]
    project = tmp_path / "审核工具 with spaces"
    project.mkdir()
    venv.EnvBuilder(with_pip=False).create(project / ".venv")
    shutil.copyfile(workspace / script, project / script)
    (project / "bootstrap.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(__file__).with_name('launched.json').write_text(json.dumps({"
        "'python': sys.executable, 'cwd': os.getcwd(), 'args': sys.argv[1:]}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    global_tools = tmp_path / "broken-global-python"
    global_tools.mkdir()
    # Mimic an unconfigured desktop: `python`/`py` cannot run, but .venv is intact.
    for name in ("python", "py"):
        (global_tools / f"{name}.bat").write_text("@exit /b 1\n", encoding="ascii")
    working_directory = tmp_path / "shortcut-working-directory"
    working_directory.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = str(global_tools) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32")
    if script == "start.bat":
        command = [os.environ["COMSPEC"], "/d", "/c", str(project / script), "--port", "18765"]
        expected = ["--port", "18765"]
    else:
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(project / script),
                   "-Port", "18765", "-Foreground"]
        expected = ["--port", "18765", "--foreground"]
    result = subprocess.run(command, cwd=working_directory, env=environment, stdin=subprocess.DEVNULL,
                            capture_output=True,
                            check=False, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, (result.stdout, result.stderr)
    launched = json.loads((project / "launched.json").read_text(encoding="utf-8"))
    assert Path(launched["python"]) == project / ".venv/Scripts/python.exe"
    assert Path(launched["cwd"]) == project
    assert launched["args"] == expected
