import sys

from mantis.core.approval_store import ApprovalStore
from mantis.core.diff_preview import build_tool_preview

TOOL_RISK_LEVELS = {
    "read_file": "LOW",
    "glob_files": "LOW",
    "grep_search": "LOW",
    "write_file": "MEDIUM",
    "edit_file": "MEDIUM",
    "run_bash": "HIGH"
}

APPROVAL_REQUIRED_TOOLS = {"write_file", "edit_file", "apply_edit", "run_bash"}


class PermissionRequiredError(RuntimeError):
    def __init__(self, approval_id: str, tool_name: str, tool_input: dict, risk_level: str):
        super().__init__(f"Approval required for {tool_name}")
        self.approval_id = approval_id
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.risk_level = risk_level


class PermissionManager:
    def __init__(self, mode: str = "default", approval_store: ApprovalStore | None = None):
        self.mode = mode.lower()
        self.approval_store = approval_store
        self.session_id = "default"
        self.job_id = None
        valid_modes = ["default", "auto", "yolo"]
        if self.mode not in valid_modes:
            raise ValueError(f"Invalid mode '{mode}'. Valid modes: {valid_modes}")

    def check(self, tool_name: str, tool_input: dict) -> bool:
        risk_level = TOOL_RISK_LEVELS.get(tool_name, "HIGH")
        
        if self.mode == "yolo":
            return True
        
        if risk_level == "LOW":
            return True
        
        if self.mode == "auto" and risk_level == "MEDIUM" and (
            self.job_id is None or tool_name not in APPROVAL_REQUIRED_TOOLS
        ):
            return True

        if self.approval_store is not None:
            approved = self.approval_store.find_approved(
                session_id=self.session_id,
                job_id=self.job_id,
                tool_name=tool_name,
                tool_input=tool_input,
            )
            if approved is not None:
                self.approval_store.update(approved.id, status="used")
                return True

            if self.job_id is not None:
                pending = self.approval_store.find_pending(
                    session_id=self.session_id,
                    job_id=self.job_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                )
                if pending is None:
                    pending = self.approval_store.create(
                        session_id=self.session_id,
                        job_id=self.job_id,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        risk_level=risk_level,
                        preview=build_tool_preview(tool_name, tool_input),
                    )
                raise PermissionRequiredError(
                    approval_id=pending.id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    risk_level=risk_level,
                )

        # For all other cases, ask the user
        return self.ask_user(tool_name, tool_input)

    def ask_user(self, tool_name: str, tool_input: dict) -> bool:
        if not sys.stdin or not sys.stdin.isatty():
            return False

        print(f"\nPermission request: {tool_name}")
        print(f"Input: {tool_input}")
        try:
            response = input("Allow execution? (y/n): ").strip().lower()
        except EOFError:
            return False
        return response in ['y', 'yes']

    def set_mode(self, mode: str):
        mode = mode.lower()
        valid_modes = ["default", "auto", "yolo"]
        if mode not in valid_modes:
            raise ValueError(f"Invalid mode '{mode}'. Valid modes: {valid_modes}")
        self.mode = mode

    def set_context(self, session_id: str = "default", job_id: str | None = None):
        self.session_id = session_id
        self.job_id = job_id
