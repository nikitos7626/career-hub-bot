"""
Base utilities, logger, and bot runtime reference resolver for auto-apply modules.
"""

import logging
import os
import sys

logger = logging.getLogger("career_hub.auto_apply")


def _get_bot_ref(attr_name, fallback):
    """
    Dynamically resolves references to functions or modules attached to the root 'bot'
    module or controller hierarchy, allowing unittest patches (e.g. @patch('bot.requests.get'))
    to work seamlessly across all submodules.
    """
    bot_mod = sys.modules.get("bot") or sys.modules.get("__main__")
    if bot_mod and hasattr(bot_mod, attr_name):
        return getattr(bot_mod, attr_name)
    bot_ctrl = sys.modules.get("career_hub.controllers.bot_controller")
    if bot_ctrl and hasattr(bot_ctrl, attr_name):
        return getattr(bot_ctrl, attr_name)
    try:
        from career_hub.controllers import bot_controller
        if hasattr(bot_controller, attr_name):
            return getattr(bot_controller, attr_name)
    except Exception:
        pass
    return fallback


def get_miniapp_url() -> str:
    """Returns the configured Mini App URL with safe environment fallback."""
    cfg_fn = _get_bot_ref("load_config", None)
    if cfg_fn:
        try:
            return cfg_fn().get("miniapp_url") or os.environ.get("MINIAPP_URL") or "http://localhost:8088/"
        except Exception:
            pass
    return os.environ.get("MINIAPP_URL") or "http://localhost:8088/"
