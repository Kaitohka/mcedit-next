"""
    appsettings
"""

import logging
from mcedit2.util.settings import Settings

log = logging.getLogger(__name__)

RecentFilesSetting = Settings().getOption('open_world_dialog/recent_files', "json", [])
EnableLightingSetting = Settings().getOption('editor/enable_lighting', bool, True)
DevModeSetting = Settings().getOption('editor/developer_mode', bool, False)
