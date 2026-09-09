"""Authoritative release-gate contract for the public MCP tool surface."""

EXPECTED_TOOL_NAMES = frozenset(
    {
        "addon_details",
        "addon_dev_loop",
        "addon_execute",
        "addon_list",
        "addon_project_map_status",
        "addon_source_inspect",
        "addon_source_tree",
        "artifact_upload_zip",
        "bridge_bootstrap_status",
        "bridge_health",
        "bridge_log_markers",
        "bridge_log_recent_errors",
        "bridge_log_tail",
        "bridge_runtime_info",
        "bridge_status",
        "bridge_write_log_marker",
        "jsonrpc_introspect",
        "kodi_album_songs",
        "kodi_artist_albums",
        "kodi_gui_action",
        "kodi_gui_screenshot",
        "kodi_gui_state",
        "kodi_library_browse",
        "kodi_library_search",
        "kodi_library_summary",
        "kodi_music_browse",
        "kodi_music_search",
        "kodi_music_summary",
        "kodi_notifications_sample",
        "kodi_player_active",
        "kodi_player_item",
        "kodi_player_open",
        "kodi_player_pause",
        "kodi_player_seek",
        "kodi_player_stop",
        "kodi_setting_get",
        "kodi_setting_set",
        "kodi_settings_list",
        "kodi_status",
        "kodi_tv_episodes",
        "kodi_tv_seasons",
        "managed_addon_build_publish_and_stage",
        "managed_addon_build_publish_stage_and_apply",
        "managed_addon_get",
        "managed_addon_list",
        "managed_addon_register",
        "managed_addon_validate_state",
        "repo_publish_artifact",
        "repo_publish_stage_apply_artifact",
        "repo_stage_and_apply_addon",
        "repo_stage_current_dev_repo",
        "repository_bootstrap_install",
        "repository_readiness",
        "target_health",
        "target_info",
        "target_list",
    }
)

# Phase 4A Batch A is intentionally limited to observational tools whose
# target dependency is already an injected JSON-RPC or bridge tool.  Future
# batches extend this set only after their dispatch paths have been migrated.
BATCH_A_TARGET_TOOL_NAMES = frozenset(
    {
        "addon_details",
        "addon_list",
        "bridge_health",
        "bridge_log_markers",
        "bridge_log_recent_errors",
        "bridge_log_tail",
        "bridge_runtime_info",
        "bridge_status",
        "jsonrpc_introspect",
        "kodi_album_songs",
        "kodi_artist_albums",
        "kodi_gui_state",
        "kodi_library_browse",
        "kodi_library_search",
        "kodi_library_summary",
        "kodi_music_browse",
        "kodi_music_search",
        "kodi_music_summary",
        "kodi_player_active",
        "kodi_player_item",
        "kodi_setting_get",
        "kodi_status",
        "kodi_tv_episodes",
        "kodi_tv_seasons",
    }
)

# Phase 4A Batch B adds only deterministic GUI/navigation and playback
# mutations. Screenshot creation, log markers, and all other mutation
# families remain deferred to later batches.
BATCH_B_TARGET_TOOL_NAMES = frozenset(
    {
        "kodi_gui_action",
        "kodi_player_open",
        "kodi_player_pause",
        "kodi_player_seek",
        "kodi_player_stop",
    }
)

# Phase 4A Batch C1 adds only the bounded, policy-controlled setting mutation.
BATCH_C1_TARGET_TOOL_NAMES = frozenset({"kodi_setting_set"})

# Phase 4A Batch C2 adds only open-world addon execution. All remaining
# target-scoped mutation families stay deferred.
BATCH_C2_TARGET_TOOL_NAMES = frozenset({"addon_execute"})

# Phase 4A Batch D1 adds only the read-only hybrid repository readiness
# comparison. Its server-local canonical repository configuration stays global.
BATCH_D1_TARGET_TOOL_NAMES = frozenset({"repository_readiness"})

EXPLICIT_TARGET_TOOL_NAMES = (
    BATCH_A_TARGET_TOOL_NAMES
    | BATCH_B_TARGET_TOOL_NAMES
    | BATCH_C1_TARGET_TOOL_NAMES
    | BATCH_C2_TARGET_TOOL_NAMES
    | BATCH_D1_TARGET_TOOL_NAMES
)

SERVER_LOCAL_TOOL_NAMES = frozenset(
    {
        "addon_project_map_status",
        "addon_source_inspect",
        "addon_source_tree",
        "artifact_upload_zip",
        "kodi_settings_list",
        "managed_addon_get",
        "managed_addon_list",
        "managed_addon_register",
        "repo_publish_artifact",
        "target_info",
        "target_list",
    }
)

HYBRID_TOOL_NAMES = frozenset(
    {
        "addon_dev_loop",
        "bridge_bootstrap_status",
        "managed_addon_build_publish_and_stage",
        "managed_addon_build_publish_stage_and_apply",
        "managed_addon_validate_state",
        "repo_publish_stage_apply_artifact",
        "repo_stage_and_apply_addon",
        "repo_stage_current_dev_repo",
        "repository_bootstrap_install",
        "repository_readiness",
    }
)

TARGET_SCOPED_TOOL_NAMES = EXPECTED_TOOL_NAMES - SERVER_LOCAL_TOOL_NAMES - HYBRID_TOOL_NAMES

assert len(EXPECTED_TOOL_NAMES) == 56
assert len(TARGET_SCOPED_TOOL_NAMES) == 35
assert len(SERVER_LOCAL_TOOL_NAMES) == 11
assert len(HYBRID_TOOL_NAMES) == 10
assert BATCH_A_TARGET_TOOL_NAMES <= TARGET_SCOPED_TOOL_NAMES
assert BATCH_B_TARGET_TOOL_NAMES <= TARGET_SCOPED_TOOL_NAMES
assert BATCH_C1_TARGET_TOOL_NAMES <= TARGET_SCOPED_TOOL_NAMES
assert BATCH_C2_TARGET_TOOL_NAMES <= TARGET_SCOPED_TOOL_NAMES
assert BATCH_D1_TARGET_TOOL_NAMES <= HYBRID_TOOL_NAMES
assert BATCH_A_TARGET_TOOL_NAMES.isdisjoint(BATCH_B_TARGET_TOOL_NAMES)
assert BATCH_C1_TARGET_TOOL_NAMES.isdisjoint(
    BATCH_A_TARGET_TOOL_NAMES | BATCH_B_TARGET_TOOL_NAMES
)
assert BATCH_C2_TARGET_TOOL_NAMES.isdisjoint(
    BATCH_A_TARGET_TOOL_NAMES | BATCH_B_TARGET_TOOL_NAMES | BATCH_C1_TARGET_TOOL_NAMES
)
assert BATCH_D1_TARGET_TOOL_NAMES.isdisjoint(
    BATCH_A_TARGET_TOOL_NAMES
    | BATCH_B_TARGET_TOOL_NAMES
    | BATCH_C1_TARGET_TOOL_NAMES
    | BATCH_C2_TARGET_TOOL_NAMES
)
