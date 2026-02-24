import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_BBDOWN_BIN = "/usr/local/bin/BBDown"
DEFAULT_BBDOWN_TIMEOUT_SECONDS = 120
DEFAULT_BBDOWN_WORK_DIR = "data/bbdown"
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa", ".json", ".json3"}


class BBDownClient:
    def __init__(self) -> None:
        self.bin_path = os.getenv("BBDOWN_BIN", DEFAULT_BBDOWN_BIN).strip() or DEFAULT_BBDOWN_BIN
        timeout_raw = os.getenv("BBDOWN_TIMEOUT_SECONDS", str(DEFAULT_BBDOWN_TIMEOUT_SECONDS))
        try:
            self.timeout_seconds = max(int(timeout_raw), 30)
        except ValueError:
            self.timeout_seconds = DEFAULT_BBDOWN_TIMEOUT_SECONDS

    @staticmethod
    def _backend_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def resolve_work_dir(self) -> Path:
        work_dir = Path(os.getenv("BBDOWN_WORK_DIR", DEFAULT_BBDOWN_WORK_DIR))
        if not work_dir.is_absolute():
            work_dir = self._backend_root() / work_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    def resolve_bin(self) -> Optional[str]:
        raw_path = self.bin_path
        if Path(raw_path).is_absolute():
            if Path(raw_path).exists():
                return raw_path
            return shutil.which(Path(raw_path).name)
        return shutil.which(raw_path)

    def get_version(self) -> Dict[str, Optional[str]]:
        resolved = self.resolve_bin()
        if not resolved:
            return {"installed": False, "version": None, "bin_path": self.bin_path}

        outputs: List[str] = []
        for args in (["--version"], ["-v"], ["--help"], []):
            try:
                result = subprocess.run(
                    [resolved, *args],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except Exception:
                continue
            output = "\n".join(
                part.strip() for part in (result.stdout or "", result.stderr or "") if part.strip()
            ).strip()
            if output:
                outputs.append(output)
            if result.returncode == 0 and output:
                break

        version = None
        for output in outputs:
            match = re.search(r"\b\d+\.\d+\.\d+(?:[-+._a-zA-Z0-9]*)?\b", output)
            if match:
                version = match.group(0)
                break
        if version is None and outputs:
            version = outputs[0].splitlines()[0].strip()

        return {
            "installed": True,
            "version": version,
            "bin_path": resolved,
        }

    @staticmethod
    def _list_subtitle_files(target_dir: Path) -> List[Path]:
        if not target_dir.exists():
            return []
        files: List[Path] = []
        for item in target_dir.rglob("*"):
            if item.is_file() and item.suffix.lower() in SUBTITLE_EXTENSIONS:
                files.append(item)
        return sorted(files)

    @staticmethod
    def _cookie_string_from_netscape_file(cookie_file: Path) -> str:
        if not cookie_file.exists():
            return ""
        pairs: List[str] = []
        try:
            for raw_line in cookie_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif line.startswith("#"):
                    continue
                parts = re.split(r"\s+", line, maxsplit=6)
                if len(parts) < 7:
                    continue
                name = parts[5].strip()
                value = parts[6].strip()
                if not name:
                    continue
                pairs.append(f"{name}={value}")
        except Exception:
            return ""
        return "; ".join(pairs)

    @staticmethod
    def _mask_sensitive_command(command: List[str]) -> List[str]:
        masked = list(command)
        for idx, arg in enumerate(masked):
            if arg == "--cookie" and idx + 1 < len(masked):
                masked[idx + 1] = "<redacted>"
        return masked

    def run_subtitle_download(
        self,
        video_url: str,
        output_dir: Path,
        cookie_file: Optional[Path],
    ) -> Dict[str, object]:
        resolved = self.resolve_bin()
        if not resolved:
            return {
                "success": False,
                "reason_code": "BBDOWN_NOT_FOUND",
                "message": f"未找到 BBDown 可执行文件: {self.bin_path}",
                "diagnostics": {"bbdown_bin": self.bin_path},
                "subtitle_files": [],
            }

        output_dir.mkdir(parents=True, exist_ok=True)
        run_dir = output_dir / f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        before_files = set(str(path) for path in self._list_subtitle_files(run_dir))

        command = [
            resolved,
            video_url,
            "--sub-only",
            "--skip-ai",
            "false",
            "--work-dir",
            str(run_dir),
        ]
        if cookie_file and cookie_file.exists():
            cookie_string = self._cookie_string_from_netscape_file(cookie_file)
            if cookie_string:
                command.extend(["--cookie", cookie_string])
        masked_command = self._mask_sensitive_command(command)

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "reason_code": "BBDOWN_TIMEOUT",
                "message": "BBDown 执行超时",
                "diagnostics": {
                    "command": masked_command,
                    "timeout_seconds": self.timeout_seconds,
                },
                "subtitle_files": [],
            }
        except Exception as exc:
            return {
                "success": False,
                "reason_code": "BBDOWN_EXEC_FAILED",
                "message": f"BBDown 执行失败: {exc}",
                "diagnostics": {"command": masked_command},
                "subtitle_files": [],
            }

        all_files = self._list_subtitle_files(run_dir)
        new_files = [str(path) for path in all_files if str(path) not in before_files]
        subtitle_files = new_files or [str(path) for path in all_files]

        success = result.returncode == 0 and bool(subtitle_files)
        if success:
            return {
                "success": True,
                "reason_code": None,
                "message": "BBDown 字幕下载完成",
                "diagnostics": {
                    "command": masked_command,
                    "returncode": result.returncode,
                    "work_dir": str(run_dir),
                    "stdout_tail": (result.stdout or "").splitlines()[-10:],
                    "stderr_tail": (result.stderr or "").splitlines()[-10:],
                },
                "subtitle_files": subtitle_files,
            }

        reason_code = "SUBTITLE_NOT_AVAILABLE" if result.returncode == 0 else "BBDOWN_EXEC_FAILED"
        message = "BBDown 未发现可用字幕文件" if result.returncode == 0 else "BBDown 下载字幕失败"
        return {
            "success": False,
            "reason_code": reason_code,
            "message": message,
            "diagnostics": {
                "command": masked_command,
                "returncode": result.returncode,
                "work_dir": str(run_dir),
                "stdout_tail": (result.stdout or "").splitlines()[-10:],
                "stderr_tail": (result.stderr or "").splitlines()[-10:],
                "subtitle_files_detected": subtitle_files,
            },
            "subtitle_files": subtitle_files,
        }
