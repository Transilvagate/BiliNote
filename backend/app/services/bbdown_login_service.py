import base64
import logging
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.services.bbdown_client import BBDownClient
from app.services.bilibili_cookie_service import BilibiliCookieService

logger = logging.getLogger(__name__)

DEFAULT_LOGIN_TIMEOUT_SECONDS = 180
DEFAULT_SESSION_TTL_SECONDS = 15 * 60
MAX_LOG_LINES = 120


@dataclass
class BBDownLoginSession:
    session_id: str
    process: subprocess.Popen
    work_dir: Path
    qrcode_path: Path
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    status: str = "STARTING"
    message: str = "登录进程启动中"
    logs: List[str] = field(default_factory=list)
    returncode: Optional[int] = None
    cookie_saved: bool = False
    error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self.process.poll() is None


class BBDownLoginService:
    def __init__(
        self,
        bbdown_client: Optional[BBDownClient] = None,
        cookie_service: Optional[BilibiliCookieService] = None,
    ) -> None:
        self.bbdown_client = bbdown_client or BBDownClient()
        self.cookie_service = cookie_service or BilibiliCookieService()
        self._sessions: Dict[str, BBDownLoginSession] = {}
        self._lock = threading.RLock()
        self.login_timeout_seconds = max(
            int(
                (str(self.bbdown_client.timeout_seconds) if self.bbdown_client.timeout_seconds else "120")
            ),
            60,
        )
        self.login_timeout_seconds = int(
            max(self.login_timeout_seconds, DEFAULT_LOGIN_TIMEOUT_SECONDS)
        )
        self.session_ttl_seconds = DEFAULT_SESSION_TTL_SECONDS

    @staticmethod
    def _format_time(timestamp: Optional[float]) -> Optional[str]:
        if not timestamp:
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))

    @staticmethod
    def _sanitize_log_line(line: str) -> str:
        value = (line or "").strip()
        if not value:
            return ""
        value = re.sub(r"(SESSDATA=)[^;\s]+", r"\1<redacted>", value, flags=re.IGNORECASE)
        value = re.sub(r"(bili_jct=)[^;\s]+", r"\1<redacted>", value, flags=re.IGNORECASE)
        value = re.sub(r"(DedeUserID=)[^;\s]+", r"\1<redacted>", value, flags=re.IGNORECASE)
        value = re.sub(r"(DedeUserID__ckMd5=)[^;\s]+", r"\1<redacted>", value, flags=re.IGNORECASE)
        return value

    def _append_log(self, session: BBDownLoginSession, line: str) -> None:
        clean = self._sanitize_log_line(line)
        if not clean:
            return
        session.logs.append(clean)
        if len(session.logs) > MAX_LOG_LINES:
            session.logs = session.logs[-MAX_LOG_LINES:]
        session.updated_at = time.time()

    @staticmethod
    def _line_has_any(line: str, keywords: List[str]) -> bool:
        content = line.lower()
        return any(keyword.lower() in content for keyword in keywords)

    def _update_status_from_log(self, session: BBDownLoginSession, line: str) -> None:
        if self._line_has_any(line, ["生成二维码成功", "请打开并扫描", "等待扫码"]):
            session.status = "WAITING_SCAN"
            session.message = "二维码已生成，请使用哔哩哔哩 App 扫码"
            return
        if self._line_has_any(line, ["已扫码", "扫码成功", "确认登录", "请在手机上确认"]):
            session.status = "SCANNED"
            session.message = "扫码成功，等待手机确认"
            return
        if self._line_has_any(line, ["登录成功", "登录完成"]):
            session.status = "CONFIRMED"
            session.message = "登录成功，正在同步 Cookie"
            return
        if self._line_has_any(line, ["二维码过期", "二维码已失效"]):
            session.status = "EXPIRED"
            session.message = "二维码已过期，请重新生成"
            return
        if self._line_has_any(line, ["登录失败", "失败", "error"]):
            if session.status not in {"CONFIRMED", "CANCELLED"}:
                session.status = "FAILED"
                session.message = "BBDown 登录失败"

    def _cleanup_sessions(self) -> None:
        now = time.time()
        with self._lock:
            to_delete: List[str] = []
            for sid, session in self._sessions.items():
                if session.running and now - session.started_at > self.login_timeout_seconds:
                    try:
                        session.process.terminate()
                    except Exception:
                        pass
                    session.status = "FAILED"
                    session.message = "登录超时，请重试"
                    session.completed_at = time.time()
                end_at = session.completed_at or session.updated_at
                if end_at and now - end_at > self.session_ttl_seconds:
                    to_delete.append(sid)
            for sid in to_delete:
                self._sessions.pop(sid, None)

    def _resolve_bbdown_data_file(self) -> Optional[Path]:
        resolved_bin = self.bbdown_client.resolve_bin()
        candidates: List[Path] = []
        if resolved_bin:
            candidates.append(Path(resolved_bin).with_name("BBDown.data"))
        candidates.append(self.bbdown_client.resolve_work_dir() / "BBDown.data")
        for path in candidates:
            if path.exists():
                return path
        return None

    def _persist_cookie_from_bbdown_data(self) -> bool:
        data_file = self._resolve_bbdown_data_file()
        if not data_file:
            return False
        try:
            cookie_text = data_file.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return False
        if not cookie_text:
            return False
        self.cookie_service.persist_bilibili_cookie(cookie_text=cookie_text)
        return True

    @staticmethod
    def _read_qrcode_base64(path: Path) -> Optional[str]:
        if not path.exists() or not path.is_file():
            return None
        try:
            return base64.b64encode(path.read_bytes()).decode("ascii")
        except Exception:
            return None

    def _build_payload(self, session: BBDownLoginSession, include_qrcode: bool = True) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "session_id": session.session_id,
            "status": session.status,
            "message": session.message,
            "cookie_saved": session.cookie_saved,
            "started_at": self._format_time(session.started_at),
            "updated_at": self._format_time(session.updated_at),
            "completed_at": self._format_time(session.completed_at),
            "logs": session.logs[-20:],
            "returncode": session.returncode,
            "error": session.error,
        }
        if include_qrcode:
            payload["qrcode_base64"] = self._read_qrcode_base64(session.qrcode_path)
        return payload

    def _find_active_session_locked(self) -> Optional[BBDownLoginSession]:
        for session in self._sessions.values():
            if session.status in {"STARTING", "WAITING_SCAN", "SCANNED"} and session.running:
                return session
        return None

    def _watch_session_output(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return

        process = session.process
        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    with self._lock:
                        current = self._sessions.get(session_id)
                        if not current:
                            break
                        self._append_log(current, raw_line)
                        self._update_status_from_log(current, raw_line)
        except Exception as exc:
            with self._lock:
                current = self._sessions.get(session_id)
                if current:
                    current.error = f"读取 BBDown 输出失败: {exc}"
                    current.status = "FAILED"
                    current.message = "读取 BBDown 输出失败"

        returncode = process.wait()
        with self._lock:
            current = self._sessions.get(session_id)
            if not current:
                return
            current.returncode = returncode
            current.updated_at = time.time()

            if current.status == "CANCELLED":
                current.completed_at = time.time()
                return

            if returncode == 0 or current.status == "CONFIRMED":
                persisted = self._persist_cookie_from_bbdown_data()
                if persisted:
                    current.cookie_saved = True
                    current.status = "CONFIRMED"
                    current.message = "登录成功，Cookie 已同步到本地文件"
                else:
                    current.status = "FAILED"
                    current.message = "登录完成，但未读取到有效 Cookie"
                    current.error = "COOKIE_SYNC_FAILED"
            elif current.status in {"EXPIRED"}:
                pass
            else:
                current.status = "FAILED"
                current.message = "BBDown 登录失败，请重试"
                if current.error is None:
                    current.error = "BBDOWN_LOGIN_FAILED"

            current.completed_at = time.time()

    def start_login(self) -> Dict[str, object]:
        self._cleanup_sessions()
        resolved_bin = self.bbdown_client.resolve_bin()
        if not resolved_bin:
            return {
                "status": "FAILED",
                "message": f"未找到 BBDown 可执行文件: {self.bbdown_client.bin_path}",
                "cookie_saved": False,
            }

        with self._lock:
            active = self._find_active_session_locked()
            if active:
                return self._build_payload(active, include_qrcode=True)

        session_id = str(uuid.uuid4())
        login_work_dir = self.bbdown_client.resolve_work_dir() / "login" / session_id
        login_work_dir.mkdir(parents=True, exist_ok=True)
        qrcode_path = login_work_dir / "qrcode.png"

        if qrcode_path.exists():
            try:
                qrcode_path.unlink()
            except Exception:
                pass

        process = subprocess.Popen(
            [resolved_bin, "login"],
            cwd=str(login_work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        session = BBDownLoginSession(
            session_id=session_id,
            process=process,
            work_dir=login_work_dir,
            qrcode_path=qrcode_path,
            message="正在生成二维码，请稍候",
        )
        with self._lock:
            self._sessions[session_id] = session

        thread = threading.Thread(
            target=self._watch_session_output,
            args=(session_id,),
            name=f"bbdown-login-{session_id}",
            daemon=True,
        )
        thread.start()

        deadline = time.time() + 8
        while time.time() < deadline:
            with self._lock:
                current = self._sessions.get(session_id)
                if not current:
                    break
                if current.qrcode_path.exists():
                    break
                if current.status in {"FAILED", "EXPIRED", "CONFIRMED"}:
                    break
            time.sleep(0.2)

        with self._lock:
            current = self._sessions.get(session_id)
            if not current:
                return {
                    "status": "FAILED",
                    "message": "登录会话已失效，请重试",
                    "cookie_saved": False,
                }
            return self._build_payload(current, include_qrcode=True)

    def get_login_status(self, session_id: Optional[str]) -> Dict[str, object]:
        self._cleanup_sessions()
        with self._lock:
            target: Optional[BBDownLoginSession] = None
            if session_id:
                target = self._sessions.get(session_id)
            else:
                target = self._find_active_session_locked()

            if not target:
                return {
                    "status": "IDLE",
                    "message": "当前没有进行中的登录会话",
                    "cookie_saved": False,
                }
            include_qrcode = target.status in {"STARTING", "WAITING_SCAN", "SCANNED"}
            return self._build_payload(target, include_qrcode=include_qrcode)

    def cancel_login(self, session_id: str) -> Dict[str, object]:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return {
                    "status": "IDLE",
                    "message": "登录会话不存在或已结束",
                    "cookie_saved": False,
                }
            if session.running:
                try:
                    session.process.terminate()
                except Exception as exc:
                    logger.warning(f"终止 BBDown 登录进程失败: {exc}")
            session.status = "CANCELLED"
            session.message = "登录已取消"
            session.completed_at = time.time()
            session.updated_at = time.time()
            return self._build_payload(session, include_qrcode=False)
