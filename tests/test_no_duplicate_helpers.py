import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Pre-existing overlaps this lot does not touch.
BASELINE = {
    "AMBIENT_PHRASES", "BACKENDS", "BATCH_CHARS", "BRIEF_KEYWORDS",
    "CAMERA_PREFIX", "CLIP_COUNTER", "CONFIG_PATH", "DEFAULT_FRAME_RATE",
    "DEFAULT_HEIGHT", "DEFAULT_IMPORTANT_MAX_S", "DEFAULT_TARGET_LUFS", "DEFAULT_TRACK",
    "DEFAULT_WIDTH", "DURATION_RE", "FPS", "FRAME_COUNT",
    "INVALID_FRAME_RATES", "JPEG_QUALITY", "LONG_CUT_PACING_S", "MAX_LINE_CHARS",
    "MINUTE_UNITS", "MOVE_ROLES", "ORDER_CHRONOLOGICAL", "ORDER_SCORE",
    "PACING_ANCHORS", "PHOTO_DEFAULT_S", "PHOTO_END_S", "PHOTO_MAX_S",
    "PHOTO_MIN_S", "PHOTO_SEGMENT_ID", "PHOTO_START_S", "REPO_ROOT",
    "SCHEMA_VERSION", "SCREEN_TOKEN", "SILENCE_FLOOR_DBFS", "SKIP_ROLES",
    "TRAVEL_LABELS", "UNIFORM_PHRASES", "VIDEO_EXTENSIONS", "_atomic_write_json",
    "_clip_tokens", "_dbfs", "_ffprobe_path", "_load_json",
    "_matches", "_median", "_runs", "_to_analysis_gray",
    "atomic_write_json", "build_candidates", "build_groups", "capture_time",
    "clamp", "clip_counter", "crop_size", "edit_summary",
    "find_clips", "find_photos", "fmt", "format_clock",
    "format_transcript", "frame_times", "get_client", "is_number",
    "is_photo_slot", "is_within", "load_brand", "load_config",
    "load_json", "normalize_timestamp", "pacing_table", "parse_duration",
    "parse_model_json", "parse_srt", "parse_time", "piecewise_expression",
    "read_frames", "read_ledger", "resolve_font", "run_command",
    "screen_length", "segment_key", "slot_screen_length",
    "spent_usd", "timestamp_seconds", "write_json",
}


def normalise_function(node):
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    text = "\n".join(ast.unparse(statement) for statement in body)
    return " ".join(text.split())


def normalise_constant(node):
    return " ".join(ast.unparse(node.value).split())


def collect(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[(node.name, normalise_function(node))] = path
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    found[(target.id, normalise_constant(node))] = path
    return found


def duplicate_names(root=ROOT):
    top_level = sorted((root / "scripts").glob("*.py"))
    common = sorted((root / "scripts" / "common").glob("*.py"))
    definitions = {}
    for path in top_level + common:
        for key, owner in collect(path).items():
            definitions.setdefault(key, set()).add(owner)
    names = set()
    for (name, _), owners in definitions.items():
        tops = {owner for owner in owners if owner.parent.name == "scripts"}
        if len(tops) >= 2:
            names.add(name)
    by_name = {}
    for (name, _), owners in definitions.items():
        by_name.setdefault(name, set()).update(owners)
    for name, owners in by_name.items():
        tops = {owner for owner in owners if owner.parent.name == "scripts"}
        commons = {owner for owner in owners if owner.parent.name == "common"}
        if tops and commons:
            names.add(name)
    return names


def test_no_new_duplicate_helpers():
    assert duplicate_names() - BASELINE == set()


def test_baseline_has_no_stale_entries():
    assert BASELINE - duplicate_names() == set()


def test_common_and_top_level_same_name_is_flagged(tmp_path):
    (tmp_path / "scripts" / "common").mkdir(parents=True)
    (tmp_path / "scripts" / "common" / "shared.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "scripts" / "tool.py").write_text(
        "def helper():\n    return 2\n", encoding="utf-8"
    )
    assert "helper" in duplicate_names(tmp_path)


MERGED_INTO_COMMON = {"snap_bounds", "_ramped_cut", "flow_decisions"}
MERGED_FORMULA = "0.5 / ("


def test_merged_helpers_are_not_reintroduced():
    """The merged names stay out of the top-level scripts and the merged speed formula is not copied back."""
    offenders = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in MERGED_INTO_COMMON and node.name != "_ramped_cut":
                offenders.append(f"{path.name}:{node.name}")
            if MERGED_FORMULA in ast.unparse(node):
                offenders.append(f"{path.name}:{node.name} copies the speed formula")
    assert offenders == []


def test_no_hand_rolled_worker_pool():
    offenders = []
    pool = ROOT / "scripts" / "common" / "pool.py"
    paths = sorted((ROOT / "scripts").glob("*.py")) + sorted(
        (ROOT / "scripts" / "common").glob("*.py")
    )
    for path in paths:
        if path == pool:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in {
                "ThreadPoolExecutor",
                "ProcessPoolExecutor",
            }:
                offenders.append(path.name)
                break
            if isinstance(node, ast.Attribute) and node.attr in {
                "ThreadPoolExecutor",
                "ProcessPoolExecutor",
            }:
                offenders.append(path.name)
                break
    assert offenders == []
