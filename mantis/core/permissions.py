import sys

TOOL_RISK_LEVELS = {
    "read_file": "LOW",
    "glob_files": "LOW",
    "grep_search": "LOW",
    "write_file": "MEDIUM",
    "edit_file": "MEDIUM",
    "run_bash": "HIGH"
}

class PermissionManager:
    def __init__(self, mode: str = "default"):
        self.mode = mode.lower()
        valid_modes = ["default", "auto", "yolo"]
        if self.mode not in valid_modes:
            raise ValueError(f"Invalid mode '{mode}'. Valid modes: {valid_modes}")

    def check(self, tool_name: str, tool_input: dict) -> bool:
        risk_level = TOOL_RISK_LEVELS.get(tool_name, "HIGH")
        
        if self.mode == "yolo":
            return True
        
        if risk_level == "LOW":
            return True
        
        if self.mode == "auto" and risk_level == "MEDIUM":
            return True
            
        # For all other cases, ask the user
        return self.ask_user(tool_name, tool_input)

    def ask_user(self, tool_name: str, tool_input: dict) -> bool:
        print(f"\nPermission request: {tool_name}")
        print(f"Input: {tool_input}")
        response = input("Allow execution? (y/n): ").strip().lower()
        return response in ['y', 'yes']

    def set_mode(self, mode: str):
        mode = mode.lower()
        valid_modes = ["default", "auto", "yolo"]
        if mode not in valid_modes:
            raise ValueError(f"Invalid mode '{mode}'. Valid modes: {valid_modes}")
        self.mode = mode
