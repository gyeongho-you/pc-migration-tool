"""컨트롤러의 '이 PC 이더넷 주소 자동 맞추기'(stage39) 부분. Controller 가 이 믹스인을 상속한다(병합 충돌을 줄이려고 별도 파일).

비시각(헤드리스)로 시험한다. 외부 호출은 lan_api(시험에서는 가짜)와 net_env(가짜 runner 가 든 net_setup.NetEnv)로 바꿔 끼운다.
화면은 이 메서드들만 부른다. 이 PC 의 이더넷 설정을 바꾸는 일이므로 사람의 확인(confirmed)이 있어야 적용한다.
"""
from __future__ import annotations

from typing import Any

from .. import errorlog
from .. import net_setup as _ns
from . import texts as T


class NetState:
    def __init__(self) -> None:
        self.plan: Any = None
        self.adapters: list[str] = []
        self.applied: dict | None = None  # 이번 실행에서 적용한 결과 {ip, adapter, role, ...}
        self.message: str = ""  # 쉬운 안내/오류 한 줄
        self.restored_note: bool = False


class NetMixin:
    net_env: Any = None  # None 이면 실제 PC. 시험에서 가짜 NetEnv 를 넣는다

    @property
    def net(self) -> NetState:
        st = self.__dict__.get("_net")
        if st is None:
            st = self.__dict__["_net"] = NetState()
        return st

    def _guard_err(self, message: str) -> Exception:
        from .controller import GuardError
        return GuardError(message)

    # ------------------------------------------------------------------ 조회(읽기 전용)
    def net_pending(self) -> dict | None:
        """프로그램이 중간에 꺼져 되돌리지 못한 변경이 있으면 요약 dict(없으면 None). 이번 실행에서 적용한 것은 제외."""
        try:
            pend = self._lan().pending_restore(self.net_env)
        except Exception as e:  # noqa: BLE001 - 조회 실패가 화면을 막지 않게
            errorlog.record(e, "이더넷 복구 기록 조회")
            return None
        if pend and not pend.get("bad") and self.net.applied and self.net.applied.get("ip") == pend.get("ip"):
            return None
        return pend

    def net_pending_text(self) -> str:
        pend = self.net_pending()
        if not pend:
            return ""
        if pend.get("bad"):
            return T.NET_PENDING_BAD
        return T.NET_PENDING.format(adapter=pend["adapter"], ip=pend["ip"])

    def net_refresh_adapters(self) -> list[str]:
        """직결 후보 어댑터 이름 목록(읽기 전용). 못 읽으면 빈 목록과 안내."""
        st = self.net
        try:
            st.adapters = [a.name for a in self._lan().list_ip_adapters(self.net_env)]
            st.message = "" if st.adapters else T.NET_NO_ADAPTER
        except _ns.NetSetupError as e:
            st.adapters, st.message = [], str(e)
        except Exception as e:  # noqa: BLE001
            errorlog.record(e, "이더넷 어댑터 조회")
            st.adapters, st.message = [], T.NET_NO_ADAPTER
        return list(st.adapters)

    def net_applied(self) -> dict | None:
        return self.net.applied

    def net_status_text(self) -> str:
        st = self.net
        if st.applied:
            lines = [T.NET_APPLIED.format(ip=st.applied["ip"], adapter=st.applied["adapter"])]
            if st.applied.get("role") == "receiver":
                lines.append(T.NET_APPLIED_RECEIVER)
            else:
                peer = _ns.band_host(_ns.band_of_address(st.applied["ip"]) or _ns.CANDIDATE_BANDS[0], "receiver")
                lines.append(T.NET_APPLIED_SENDER.format(peer=peer))
            lines.append(T.NET_AUTO_RESTORE_NOTE)
            return "\n".join(lines)
        if st.restored_note:
            return T.NET_RESTORED
        return st.message or T.NET_NOT_APPLIED

    # ------------------------------------------------------------------ 계획
    def net_peer(self) -> str | None:
        """보내는 쪽: 입력했거나 찾은 받는 쪽 주소가 자동 맞추기 대역이면 그 주소(그 대역에 맞추기 위해)."""
        t = (self.files.target or "").strip()
        return t if t and _ns.band_of_address(t) else None

    def net_plan(self, role: str, adapter: str | None = None) -> str:
        """계획을 만들고(읽기 전용) 확인창에 보일 설명을 돌려준다. 만들 수 없으면 GuardError(쉬운 문구)."""
        peer = self.net_peer() if role == "sender" else None
        try:
            plan = self._lan().plan_ip_setup(role, adapter or None, peer, env=self.net_env)
        except _ns.NetSetupError as e:
            self.net.plan = None
            raise self._guard_err(str(e)) from e
        self.net.plan = plan
        return plan.describe()

    def net_plan_text(self) -> str:
        return self.net.plan.describe() if self.net.plan is not None else ""

    # ------------------------------------------------------------------ 적용 / 되돌리기
    def net_apply(self, confirmed: bool = False) -> None:
        """계획 보기를 한 뒤, 사람이 확인(confirmed)하면 적용한다. 관리자 권한 확인창이 뜰 수 있어 작업으로 돌린다."""
        plan = self.net.plan
        if plan is None:
            raise self._guard_err(T.NET_PLAN_FIRST)
        if not confirmed:
            raise self._guard_err(T.NET_NEED_CONFIRM)
        lan, st = self._lan(), self.net

        def work(progress, token):
            try:
                return {"result": lan.apply_ip_setup(plan, lambda _t: True, self.net_env)}
            except _ns.NetSetupError as e:
                return {"error": str(e)}

        def done(res):
            if res.get("error"):
                st.applied, st.message, st.restored_note = None, res["error"], False
            else:
                st.applied, st.message, st.restored_note = dict(res["result"]), "", False
            self.emit("net_changed", res)
        self._run(T.TASK_NET_APPLY, work, done)

    def net_restore(self) -> None:
        """원래 설정으로 되돌린다(이번에 적용한 것이든 중간에 꺼져 남은 것이든 같은 복구 기록으로)."""
        lan, st = self._lan(), self.net

        def work(progress, token):
            try:
                return {"result": lan.restore_ip_setup(self.net_env)}
            except _ns.NetSetupError as e:
                return {"error": str(e)}

        def done(res):
            if res.get("error"):
                st.message, st.restored_note = res["error"], False
            else:
                st.applied, st.plan, st.message, st.restored_note = None, None, "", True
            self.emit("net_changed", res)
        self._run(T.TASK_NET_RESTORE, work, done)

    def net_restore_if_applied(self) -> bool:
        """전송이 끝났을 때 이번에 맞춘 주소가 있으면 자동으로 되돌린다(화면이 호출). 되돌릴 일이 있었으면 True."""
        if not self.net.applied:
            return False
        try:
            self.net_restore()
        except Exception:  # noqa: BLE001 - 바쁘면 다음 기회에: 단추로 되돌릴 수 있다
            return False
        return True
